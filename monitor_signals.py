# -*- coding: utf-8 -*-
import pandas as pd
import numpy as np
from binance.client import Client
import json
import time
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv
import os
import traceback # Para depuración
import logging # ### CAMBIO: Importar logging

load_dotenv()
# ==============================================================================
# 0. 🪵 CONFIGURACIÓN DEL LOGGING
# ==============================================================================
### CAMBIO: Configurar el logging para guardar en archivo
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
log_handler = logging.FileHandler('bot_activity.log', mode='a') # 'a' para añadir al archivo existente
log_handler.setFormatter(log_formatter)

logger = logging.getLogger()
logger.setLevel(logging.INFO) # Nivel mínimo de mensajes a registrar (INFO, WARNING, ERROR, DEBUG)
logger.addHandler(log_handler)

# Opcional: Si también quieres ver los logs en consola mientras pruebas
# console_handler = logging.StreamHandler()
# console_handler.setFormatter(log_formatter)
# logger.addHandler(console_handler)


# ==============================================================================
# 1. ⚙️ CONFIGURACIÓN Y ESTADO GLOBAL
# ==============================================================================
API_KEY = os.getenv("API_KEY")
SECRET_KEY = os.getenv("SECRET_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not API_KEY or not SECRET_KEY:
    logger.error("Las claves API_KEY o SECRET_KEY no se encontraron en el archivo .env.") # ### CAMBIO: Usar logger.error
    raise ValueError("ERROR: Las claves API_KEY o SECRET_KEY no se encontraron en el archivo .env.")
# Ajustar timeout para llamadas a la API (ej. 60 segundos)
client = Client(API_KEY, SECRET_KEY, {"timeout": 60})


# Nombres de archivos
SYMBOLS_FILE = 'top_100_symbols.json'
PIVOTS_FILE = 'daily_pivots.json'
TRADES_FILE = 'active_trades.json'
CLOSED_TRADES_FILE = 'closed_trades.json'
HISTORICO_CSV_FILE = 'historico_trades.csv'

INTERVALO_MONITOREO_SEG = 900 # 15 minutos
EFFICIENCY_RATIO_PERIOD = 20 # Periodo para el Ratio de Eficiencia

# ==============================================================================
# 2. 🧮 FÓRMULAS Y UTILIDADES
# ==============================================================================

def calculate_pivots_fibonacci(high, low, close):
    # Asegurarse de que high >= low
    if high < low: high, low = low, high
    rango = high - low
    PP = (high + low + close) / 3
    FIB_382, FIB_618, FIB_100 = 0.382, 0.618, 1.000
    R1 = PP + (rango * FIB_382); R2 = PP + (rango * FIB_618); R3 = PP + (rango * FIB_100)
    S1 = PP - (rango * FIB_382); S2 = PP - (rango * FIB_618); S3 = PP - (rango * FIB_100)
    return {k: round(v, 4) for k, v in {'PP': PP, 'R1': R1, 'R2': R2, 'R3': R3, 'S1': S1, 'S2': S2, 'S3': S3}.items()}

def enviar_telegram(mensaje):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': mensaje, 'parse_mode': 'Markdown'}
    try: requests.post(url, data=payload, timeout=10)
    except Exception as e: logger.error(f"Error al enviar mensaje a Telegram: {e}") # ### CAMBIO: Usar logger.error

# Funciones de persistencia de estado de operaciones

def load_active_trades():
    try:
        if os.path.exists(TRADES_FILE) and os.path.getsize(TRADES_FILE) > 0:
            with open(TRADES_FILE, 'r') as f: return json.load(f)
        return {}
    except json.JSONDecodeError:
        logger.warning(f"Error al decodificar {TRADES_FILE}. Archivo corrupto?. Usando diccionario vacío.") # ### CAMBIO: Usar logger.warning
        return {}
    except Exception as e:
        logger.error(f"Error al cargar {TRADES_FILE}: {e}") # ### CAMBIO: Usar logger.error
        return {}


def save_active_trades(trades):
    def enhanced_json_converter(obj):
        if isinstance(obj, np.integer): return int(obj)
        if isinstance(obj, np.floating): return float(obj)
        if isinstance(obj, np.bool_): return bool(obj)
        if isinstance(obj, (datetime, pd.Timestamp)): return obj.isoformat()
        raise TypeError(f'Object of type {obj.__class__.__name__} is not JSON serializable')
    try:
        temp_file = TRADES_FILE + ".tmp"
        with open(temp_file, 'w') as f: json.dump(trades, f, indent=4, default=enhanced_json_converter)
        os.replace(temp_file, TRADES_FILE)
    except Exception as e:
        logger.error(f"Error al guardar {TRADES_FILE}: {e}") # ### CAMBIO: Usar logger.error
        if os.path.exists(temp_file):
            try: os.remove(temp_file)
            except Exception as rem_e: logger.error(f"No se pudo eliminar archivo temporal {temp_file}: {rem_e}") # ### CAMBIO: Usar logger.error

def load_closed_trades():
    try:
        if os.path.exists(CLOSED_TRADES_FILE) and os.path.getsize(CLOSED_TRADES_FILE) > 0:
            with open(CLOSED_TRADES_FILE, 'r') as f: return json.load(f)
        return []
    except json.JSONDecodeError:
        logger.warning(f"Error al decodificar {CLOSED_TRADES_FILE}. Archivo corrupto?. Usando lista vacía.") # ### CAMBIO: Usar logger.warning
        return []
    except Exception as e:
        logger.error(f"Error al cargar {CLOSED_TRADES_FILE}: {e}") # ### CAMBIO: Usar logger.error
        return []

def save_closed_trades(trades_list):
    """Guarda el historial en JSON y también en un archivo CSV para análisis en Excel."""
    try:
        temp_file_json = CLOSED_TRADES_FILE + ".tmp"
        with open(temp_file_json, 'w') as f: json.dump(trades_list, f, indent=4, default=str)
        os.replace(temp_file_json, CLOSED_TRADES_FILE)
    except Exception as e:
        logger.error(f"Error al guardar {CLOSED_TRADES_FILE}: {e}") # ### CAMBIO: Usar logger.error
        if os.path.exists(temp_file_json):
            try: os.remove(temp_file_json)
            except Exception as rem_e: logger.error(f"No se pudo eliminar archivo temporal {temp_file_json}: {rem_e}") # ### CAMBIO: Usar logger.error


    if trades_list:
        try:
            df = pd.DataFrame(trades_list)

            ### AJUSTE 4: GUARDADO COMPLETO DEL CSV (CORREGIDO) ###
            # Guarda todas las columnas del DataFrame, en lugar de una lista fija.
            df_ordered = df

            temp_file_csv = HISTORICO_CSV_FILE + ".tmp"
            df_ordered.to_csv(temp_file_csv, index=False, mode='w')
            os.replace(temp_file_csv, HISTORICO_CSV_FILE)
            logger.info(f"Historial actualizado en {HISTORICO_CSV_FILE}") # ### CAMBIO: Usar logger.info
        except Exception as e:
            logger.error(f"Error al guardar historial en CSV: {e}") # ### CAMBIO: Usar logger.error
            if os.path.exists(temp_file_csv):
                 try: os.remove(temp_file_csv)
                 except Exception as rem_e: logger.error(f"No se pudo eliminar archivo temporal {temp_file_csv}: {rem_e}") # ### CAMBIO: Usar logger.error

# ==============================================================================
# 3. 💾 FUNCIÓN DE ACTUALIZACIÓN DIARIA DE PIVOTES Y RESUMEN
# ==============================================================================

def actualizar_pivotes_diarios():
    """Calcula Pivotes y envía resumen diario sin borrar el historial."""
    try:
        if not os.path.exists(SYMBOLS_FILE):
             logger.error(f"Archivo {SYMBOLS_FILE} no encontrado. Ejecuta 'escaneo_inicial.py'."); return False # ### CAMBIO: Usar logger.error
        with open(SYMBOLS_FILE, 'r') as f: symbols = json.load(f)
        if not symbols:
             logger.error(f"Archivo {SYMBOLS_FILE} está vacío."); return False # ### CAMBIO: Usar logger.error
    except (json.JSONDecodeError, Exception) as e:
         logger.error(f"Error al leer {SYMBOLS_FILE}: {e}"); return False # ### CAMBIO: Usar logger.error

    all_closed_trades = load_closed_trades()
    yesterday_utc_str = (datetime.now(timezone.utc) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    trades_de_ayer = [t for t in all_closed_trades if isinstance(t.get('close_date'), str) and t['close_date'].startswith(yesterday_utc_str)]

    if trades_de_ayer:
        ganadoras = sum(1 for t in trades_de_ayer if t.get('status') == 'CLOSED_TP')
        perdedoras = sum(1 for t in trades_de_ayer if t.get('status') == 'CLOSED_SL')
        mensaje = (f"📊 **RESUMEN ({yesterday_utc_str})** 📊\n"
                   f"G: {ganadoras} | P: {perdedoras} | T: {len(trades_de_ayer)}")
        enviar_telegram(mensaje)

    all_pivots = {}
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    logger.info(f"Iniciando cálculo de Pivotes para {today_utc}") # ### CAMBIO: Usar logger.info
    symbols_processed = 0
    for symbol in symbols:
        try:
            klines_daily = client.futures_historical_klines(symbol, Client.KLINE_INTERVAL_1DAY, "2 day ago UTC", limit=2)
            if len(klines_daily) < 2: continue
            high_d, low_d, close_d = [float(klines_daily[-2][i]) for i in [2, 3, 4]]
            pivotes = calculate_pivots_fibonacci(high_d, low_d, close_d)
            all_pivots[symbol] = {'date': today_utc, 'levels': pivotes}
            symbols_processed += 1
            if symbols_processed % 50 == 0: logger.info(f"   ...Calculando Pivotes {symbols_processed}/{len(symbols)}") # ### CAMBIO: Usar logger.info
        except Exception as e:
            logger.warning(f"Error calculando Pivotes para {symbol}: {e}") # ### CAMBIO: Usar logger.warning
            time.sleep(1)

    if all_pivots:
        try:
            with open(PIVOTS_FILE, 'w') as f: json.dump(all_pivots, f, indent=4)
            logger.info(f"{len(all_pivots)} Pivotes guardados en {PIVOTS_FILE}") # ### CAMBIO: Usar logger.info
            enviar_telegram(f"⭐️ **PIVOTES ACTUALIZADOS** {today_utc} ({len(all_pivots)} pares).")
        except Exception as e:
            logger.error(f"Error al guardar {PIVOTS_FILE}: {e}") # ### CAMBIO: Usar logger.error
            return False
    else:
        logger.warning(f"No se calcularon pivotes para {today_utc}") # ### CAMBIO: Usar logger.warning
        enviar_telegram(f"⚠️ **ERROR PIVOTES:** No se pudieron calcular los pivotes para {today_utc}.")
        return False
    return True

def verificar_y_actualizar_pivotes():
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        if os.path.exists(PIVOTS_FILE) and os.path.getsize(PIVOTS_FILE) > 0:
            with open(PIVOTS_FILE, 'r') as f:
                try:
                    daily_data = json.load(f)
                    if daily_data and any(data.get('date') == today_utc for data in daily_data.values()):
                         # logger.info(f"Pivotes para {today_utc} ya existen.") # Opcional: menos verboso
                         return True
                except json.JSONDecodeError:
                    logger.warning(f"Archivo {PIVOTS_FILE} corrupto. Recalculando...") # ### CAMBIO: Usar logger.warning
        else:
             logger.info(f"Archivo {PIVOTS_FILE} no encontrado o vacío. Calculando...") # ### CAMBIO: Usar logger.info
        return actualizar_pivotes_diarios()
    except Exception as e:
        logger.error(f"Error inesperado al verificar pivotes: {e}. Intentando calcular...") # ### CAMBIO: Usar logger.error
        return actualizar_pivotes_diarios()

# ==============================================================================
# 4. 📈 LÓGICA DE SEGUIMIENTO DE OPERACIONES (TP/SL)
# ==============================================================================

def check_active_trades(all_pivots):
    active_trades = load_active_trades()
    if not active_trades: return

    updated_trades = active_trades.copy()
    closed_trades_list = load_closed_trades()
    trades_closed_in_cycle = False

    for symbol, trade in active_trades.items():
        if trade.get('status') != 'OPEN': continue
        try:
            klines_15m = client.futures_historical_klines(symbol, Client.KLINE_INTERVAL_15MINUTE, "15m ago UTC", limit=1)
            if not klines_15m: continue
            price = float(klines_15m[-1][4])
            pivotes = all_pivots.get(symbol, {}).get('levels', {})
            required_keys = [trade.get('tp1_key'), trade.get('tp2_key'), trade.get('sl_key')]
            if not pivotes or not all(key and key in pivotes for key in required_keys):
                 logger.warning(f"Faltan niveles de pivote o claves para {symbol}. Trade: {trade.get('entry_date')}") # ### CAMBIO: Usar logger.warning
                 continue

            tp1_level, tp2_level, sl_level = pivotes[trade['tp1_key']], pivotes[trade['tp2_key']], pivotes[trade['sl_key']]
            is_long = trade['entry_type'] == 'LONG'

            ### AJUSTE 1 (DATO-CRÍTICO): MOVER SL A BREAK-EVEN EN TP1 ###
            if trade.get('tp1_hit', False):
                sl_level = trade['entry_price']
            #############################################

            # SL Check
            if (is_long and price < sl_level) or (not is_long and price > sl_level):
                mensaje = f"🛑 *SL {trade['entry_type']} {symbol}* | P: {price:.4f} SL: {sl_level:.4f}"
                enviar_telegram(mensaje)
                logger.info(f"SL alcanzado para {symbol} ({trade['entry_type']}) a {price:.4f}") # ### CAMBIO: Usar logger.info
                trade.update({'status': 'CLOSED_SL', 'close_price': price, 'close_date': datetime.now(timezone.utc).isoformat(), 'symbol': symbol})
                closed_trades_list.append(trade); del updated_trades[symbol]; trades_closed_in_cycle = True; continue

            # TP2 Check
            if (is_long and price > tp2_level) or (not is_long and price < tp2_level):
                if symbol in updated_trades and not updated_trades[symbol].get('tp1_hit'):
                     updated_trades[symbol]['tp1_hit'] = True
                mensaje = f"🎯 *TP2 {trade['entry_type']} {symbol}* | P: {price:.4f} TP2: {tp2_level:.4f}"
                enviar_telegram(mensaje)
                logger.info(f"TP2 alcanzado para {symbol} ({trade['entry_type']}) a {price:.4f}") # ### CAMBIO: Usar logger.info
                trade.update({'status': 'CLOSED_TP', 'tp2_hit': True, 'close_price': price, 'close_date': datetime.now(timezone.utc).isoformat(), 'symbol': symbol})
                closed_trades_list.append(trade); del updated_trades[symbol]; trades_closed_in_cycle = True; continue

            # TP1 Check
            if (is_long and price > tp1_level) or (not is_long and price < tp1_level):
                if symbol in updated_trades and not updated_trades[symbol].get('tp1_hit'):
                    mensaje = f"✅ *TP1 {trade['entry_type']} {symbol}* | P: {price:.4f} TP1: {tp1_level:.4f}"
                    enviar_telegram(mensaje); updated_trades[symbol]['tp1_hit'] = True
                    logger.info(f"TP1 alcanzado para {symbol} ({trade['entry_type']}) a {price:.4f}") # ### CAMBIO: Usar logger.info
        except Exception as e: logger.error(f"Error chequeando trade activo {symbol}: {e}\n{traceback.format_exc()}") # ### CAMBIO: Usar logger.error con traceback

    save_active_trades(updated_trades)
    if trades_closed_in_cycle:
        current_closed = load_closed_trades()
        existing = {(t.get('symbol'), t.get('entry_date')) for t in current_closed}
        newly_closed = [t for t in closed_trades_list if t.get('status','').startswith('CLOSED') and (t.get('symbol'), t.get('entry_date')) not in existing]
        if newly_closed:
            current_closed.extend(newly_closed)
            save_closed_trades(current_closed)


# ==============================================================================
# 5. 🚦 DETECCIÓN DE NUEVAS SEÑALES (CON LÓGICA MEJORADA)
# ==============================================================================

def calculate_adx(df, period=14):
    df['Prev_Close'] = df['Close'].shift(1); df['Prev_High'] = df['High'].shift(1); df['Prev_Low'] = df['Low'].shift(1)
    df['High-Low'] = df['High'] - df['Low']
    df['High-PrevClose'] = abs(df['High'] - df['Prev_Close'])
    df['Low-PrevClose'] = abs(df['Low'] - df['Prev_Close'])
    df['TR'] = df[['High-Low', 'High-PrevClose', 'Low-PrevClose']].max(axis=1).fillna(0)
    move_up = df['High'] - df['Prev_High']; move_down = df['Prev_Low'] - df['Low']
    df['+DM'] = np.where((move_up > move_down) & (move_up > 0), move_up, 0)
    df['-DM'] = np.where((move_down > move_up) & (move_down > 0), move_down, 0)
    alpha = 1 / period
    TR_smooth = df['TR'].ewm(alpha=alpha, adjust=False).mean()
    DM_plus_smooth = df['+DM'].ewm(alpha=alpha, adjust=False).mean(); DM_minus_smooth = df['-DM'].ewm(alpha=alpha, adjust=False).mean()
    df['DI_plus'] = np.where(TR_smooth != 0, (DM_plus_smooth / TR_smooth) * 100, 0)
    df['DI_minus'] = np.where(TR_smooth != 0, (DM_minus_smooth / TR_smooth) * 100, 0)
    DI_diff = abs(df['DI_plus'] - df['DI_minus']); DI_sum = df['DI_plus'] + df['DI_minus']
    df['DX'] = np.where(DI_sum != 0, (DI_diff / DI_sum) * 100, 0)
    df['ADX'] = df['DX'].ewm(alpha=alpha, adjust=False).mean()
    df.drop(columns=['Prev_Close', 'Prev_High', 'Prev_Low', 'High-Low', 'High-PrevClose', 'Low-PrevClose', 'TR', '+DM', '-DM', 'DX'], inplace=True, errors='ignore') # errors='ignore'
    return df


def calculate_efficiency_ratio(series, period):
    """Calcula el Ratio de Eficiencia."""
    if not isinstance(series, pd.Series): series = pd.Series(series) # Asegurar que es Series
    if series.isnull().any() or len(series) < period + 1: return np.nan
    net_change = abs(series.iloc[-1] - series.iloc[-(period + 1)])
    sum_of_moves = abs(series.diff()).iloc[-period:].sum()
    return net_change / sum_of_moves if sum_of_moves != 0 else 0


def get_h1_trend_alignment(symbol, entry_type):
    """Verifica si la tendencia H1 se alinea con la señal M15."""
    try:
        klines_h1 = client.futures_historical_klines(symbol, Client.KLINE_INTERVAL_1HOUR, "5 day ago UTC", limit=100)
        if len(klines_h1) < 51: return None # Aumentar si se necesita más historial para EMAs/MACD

        df_h1 = pd.DataFrame(klines_h1, columns=['open_time','O','H','L','C','V','ct','qav','nt','tbbav','tbqav','ig'])
        df_h1.rename(columns={'C': 'Close'}, inplace=True)
        df_h1['Close'] = df_h1['Close'].astype(float)
        df_h1.dropna(subset=['Close'], inplace=True)
        if df_h1.empty or len(df_h1) < 51: return None

        df_h1['EMA50_H1'] = df_h1['Close'].ewm(span=50, adjust=False).mean()
        ema12_h1 = df_h1['Close'].ewm(span=12, adjust=False).mean(); ema26_h1 = df_h1['Close'].ewm(span=26, adjust=False).mean()
        macd_line_h1 = ema12_h1 - ema26_h1; macd_signal_h1 = macd_line_h1.ewm(span=9, adjust=False).mean()
        macd_hist_h1 = macd_line_h1 - macd_signal_h1

        # Verificar NaNs en la última fila calculada
        if macd_hist_h1.isnull().iloc[-1] or df_h1['EMA50_H1'].isnull().iloc[-1]: return None

        last_h1 = df_h1.iloc[-1]
        last_macd_hist_h1 = macd_hist_h1.iloc[-1]

        if entry_type == 'LONG':
            return last_h1['Close'] > last_h1['EMA50_H1'] and last_macd_hist_h1 > 0
        elif entry_type == 'SHORT':
            return last_h1['Close'] < last_h1['EMA50_H1'] and last_macd_hist_h1 < 0
        else: return None
    except Exception as e:
        logger.warning(f"Error obteniendo alineación H1 para {symbol}: {e}") # ### CAMBIO: Usar logger.warning
        return None

### ===========================================================================
### ### NUEVA FUNCIÓN (Punto 4 + 1): Filtro de Horario y Top-Down (RSI Diario)
### ===========================================================================
def get_market_condition(symbol):
    """
    Evalúa condiciones generales del mercado para el par:
    1. Filtro de Horario: No operar después de las 17:00 UTC.
    2. Filtro Top-Down: No comprar (LONG) si el RSI Diario > 75.
    Retorna (favorable_para_long, favorable_para_short)
    """
    try:
        # --- 1. FILTRO DE HORARIO ---
        hora_actual_utc = datetime.now(timezone.utc).hour
        if hora_actual_utc >= 17: # 17:00 UTC
            logger.info(f"Filtro de horario: Pausando nuevas señales para {symbol} (después de las 17:00 UTC).")
            return False, False # Desfavorable para ambos

        # --- 2. FILTRO RSI DIARIO (Top-Down) ---
        klines_daily = client.futures_historical_klines(symbol, Client.KLINE_INTERVAL_1DAY, "30 day ago UTC", limit=30)
        if len(klines_daily) < 20: 
            return True, True # Favorable si no hay datos suficientes

        df_daily = pd.DataFrame(klines_daily, columns=['ot','O','H','L','C','V','ct','qav','nt','tbbav','tbqav','ig'])
        df_daily[['Close']] = df_daily[['C']].astype(float)
        
        # RSI diario
        delta = df_daily['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi_daily = 100 - (100 / (1 + rs))
        
        last_rsi_daily = rsi_daily.iloc[-1]
        
        # No operar LONG si RSI diario > 75 (sobrecompra extrema)
        if last_rsi_daily > 75:
            logger.info(f"Descartando LONG en {symbol} - RSI Diario muy alto ({last_rsi_daily:.1f})")
            return False, True # Desfavorable para LONG, ok para SHORT
            
        return True, True # Favorable para ambos

    except Exception as e:
        logger.warning(f"Error evaluando condición de mercado para {symbol}: {e}")
        return True, True # Ser permisivo si la API falla


def detect_new_signals(all_pivots):
    active_trades = load_active_trades()
    symbols_to_check = list(all_pivots.keys())

    for symbol in symbols_to_check:
        if symbol in active_trades: continue
        
        ### ====================================================== ###
        ### ### NUEVO FILTRO DE HORARIO Y TOP-DOWN (Punto 1 y 4) ### ###
        ### ====================================================== ###
        favorable_para_long, favorable_para_short = get_market_condition(symbol)
        if not favorable_para_long and not favorable_para_short:
            # Filtro de horario (17:00+ UTC) activado
            continue # Saltar al siguiente símbolo
        ##############################################################

        pivot_data = all_pivots.get(symbol)
        if not pivot_data: continue
        pivotes = pivot_data.get('levels')
        if not pivotes or not all(k in pivotes for k in ['R1', 'R2', 'R3', 'S1', 'PP']): continue
        R1, R2, R3, S1, PP = pivotes['R1'], pivotes['R2'], pivotes['R3'], pivotes['S1'], pivotes['PP']

        try:
            klines_15m = client.futures_historical_klines(symbol, Client.KLINE_INTERVAL_15MINUTE, "55 hour ago UTC", limit=250)
            if len(klines_15m) < 201: continue

            df = pd.DataFrame(klines_15m, columns=['ot', 'O', 'H', 'L', 'C', 'V', 'ct', 'qav','nt','tbbav','tbqav','ig'])
            df.rename(columns={'C': 'Close', 'V': 'Volume', 'H': 'High', 'L': 'Low'}, inplace=True)
            df[['Close', 'Volume', 'High', 'Low']] = df[['Close', 'Volume', 'High', 'Low']].astype(float)
            df.dropna(subset=['Close'], inplace=True)
            if len(df) < 201: continue

            # --- CÁLCULO DE INDICADORES M15 ---
            df['EMA8'] = df['Close'].ewm(span=8, adjust=False).mean()
            df['EMA24'] = df['Close'].ewm(span=24, adjust=False).mean()
            df['EMA50'] = df['Close'].ewm(span=50, adjust=False).mean()
            df['EMA100'] = df['Close'].ewm(span=100, adjust=False).mean()
            df['EMA200'] = df['Close'].ewm(span=200, adjust=False).mean()
            delta = df['Close'].diff(); gain = (delta.where(delta > 0, 0)).rolling(window=14).mean(); loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            df['RSI'] = 100 - (100 / (1 + rs.replace([np.inf, -np.inf], np.nan)))
            
            ### AJUSTE 3 (DATO-CRÍTICO): CORRECCIÓN ADVERTENCIA PANDAS ###
            df['RSI'] = df['RSI'].ffill()
            
            df['EMA12'] = df['Close'].ewm(span=12, adjust=False).mean(); df['EMA26'] = df['Close'].ewm(span=26, adjust=False).mean()
            macd_line = df['EMA12'] - df['EMA26']; macd_signal = macd_line.ewm(span=9, adjust=False).mean()
            df['MACD_hist'] = macd_line - macd_signal
            df['BB_middle'] = df['Close'].rolling(window=20).mean(); std_dev = df['Close'].rolling(window=20).std()
            df['BB_upper'] = df['BB_middle'] + (std_dev * 2); df['BB_lower'] = df['BB_middle'] - (std_dev * 2)
            df['Volume_MA20'] = df['Volume'].rolling(window=20).mean()
            df = calculate_adx(df, period=14)
            df['Efficiency_Ratio'] = df['Close'].rolling(window=EFFICIENCY_RATIO_PERIOD + 1).apply(lambda x: calculate_efficiency_ratio(pd.Series(x), EFFICIENCY_RATIO_PERIOD))

            # --- DATOS DE LA ÚLTIMA VELA ---
            if len(df) < 2: continue
            last, prev = df.iloc[-1], df.iloc[-2]
            required_indicators = ['ADX', 'RSI', 'MACD_hist', 'BB_upper', 'Efficiency_Ratio', 'EMA8', 'EMA24', 'EMA50', 'EMA100', 'EMA200', 'Volume_MA20']
            if last[required_indicators].isnull().any(): continue

            price_last_closed = last['Close']; bb_upper_actual = last['BB_upper']; adx_actual = last['ADX']
            rsi_actual = last['RSI']; macd_hist_actual = last['MACD_hist']; ema8_below_24 = last['EMA8'] < last['EMA24']
            efficiency_ratio_actual = last['Efficiency_Ratio']
            
            # Calcular vol_ratio (Punto 2)
            vol_ratio = last['Volume'] / last['Volume_MA20'] if (last['Volume_MA20'] is not None and last['Volume_MA20'] > 0) else 0

            cruce_alcista = (prev['EMA24'] < prev['EMA50']) and (last['EMA24'] > last['EMA50'])
            cruce_bajista = (prev['EMA24'] > prev['EMA50']) and (last['EMA24'] < last['EMA50'])

            # --- LÓGICA DE ENTRADA CON FILTROS ACTIVOS Y DATOS OBSERVACIONALES ---

            entry_signal = False
            entry_type = None

            ### ======================================================= ###
            ### ### AJUSTE: NUEVOS FILTROS LONG (Puntos 2, 3, 4)      ### ###
            ### ======================================================= ###
            if (favorable_para_long and cruce_alcista and 
                (S1 < price_last_closed < R1) and macd_hist_actual > 0 and
                (rsi_actual > 50 and rsi_actual < 67) and  # <-- Punto 3: Filtro RSI (67)
                vol_ratio > 1.0 and                       # <-- Punto 2: Filtro Volumen
                adx_actual > 25 and price_last_closed < bb_upper_actual):
                
                entry_signal = True; entry_type = 'LONG'

            ### ======================================================= ###
            ### ### AJUSTE: NUEVO FILTRO SHORT (Sugerencia EMA 200)   ### ###
            ### ======================================================= ###
            elif (favorable_para_short and cruce_bajista and
                  (R1 < price_last_closed < R3) and
                  price_last_closed < last['EMA200']): # <-- ¡NUEVO FILTRO DE CONTEXTO EMA 200!
                
                entry_signal = True; entry_type = 'SHORT'
                # (Mantenemos la lógica de recolección de datos simple para SHORTS)


            # Si se detectó una señal válida, obtener datos adicionales y guardar
            if entry_signal:
                h1_aligned = get_h1_trend_alignment(symbol, entry_type)

                vol_hora_ant = df['Volume'].iloc[-5:-1].mean()
                vol_pct_change = ((last['Volume'] - vol_hora_ant) / vol_hora_ant) * 100 if vol_hora_ant > 0 else 0
                
                short_zone = None # short_zone se define aquí para que exista siempre
                if entry_type == 'SHORT':
                    if price_last_closed > R2: short_zone = "Above R2"
                    elif price_last_closed > R1: short_zone = "Above R1"

                # Guardar TODOS los datos calculados, independientemente de si se usaron como filtro
                new_trade_data = {
                    'status': 'OPEN', 'entry_price': price_last_closed,
                    'tp1_hit': False, 'tp2_hit': False, 'entry_date': datetime.now(timezone.utc).isoformat(),
                    'vol_pct_change_entry': round(vol_pct_change, 2),
                    'ema_100_context': price_last_closed > last['EMA100'],
                    'ema_200_context': price_last_closed > last['EMA200'],
                    'vol_ratio_entry': round(vol_ratio, 2),
                    'rsi_entry': round(rsi_actual, 2),
                    'macd_hist_entry': round(macd_hist_actual, 6),
                    'bb_upper_entry': round(bb_upper_actual, 4),
                    'bb_lower_entry': round(last['BB_lower'], 4) if pd.notna(last['BB_lower']) else None,
                    'adx_entry': round(adx_actual, 2),
                    'plus_di_entry': round(last['DI_plus'], 2) if pd.notna(last['DI_plus']) else None,
                    'minus_di_entry': round(last['DI_minus'], 2) if pd.notna(last['DI_minus']) else None,
                    'ema_8_below_24_entry': ema8_below_24,
                    'short_entry_zone': short_zone,
                    'efficiency_ratio_entry': round(efficiency_ratio_actual, 3),
                    'h1_trend_aligned_entry': h1_aligned,
                    'entry_type': entry_type
                }

                if entry_type == 'LONG':
                    new_trade_data.update({'tp1_key': 'R1', 'tp2_key': 'R2', 'sl_key': 'S1'})
                    mensaje = (f"🚀 *Compra {symbol}* | P:{price_last_closed:.4f} RSI:{rsi_actual:.1f} ADX:{adx_actual:.1f} VolR:{vol_ratio:.1f}")
                    log_msg = f"Nueva COMPRA detectada: {symbol} @ {price_last_closed:.4f}"
                
                elif entry_type == 'SHORT':
                    new_trade_data.update({'tp1_key': 'PP', 'tp2_key': 'S1', 'sl_key': 'R2'})
                    mensaje = (f"🔻 *Venta {symbol}* | P:{price_last_closed:.4f} RSI:{rsi_actual:.1f} ADX:{adx_actual:.1f} Z:{short_zone}")
                    log_msg = f"Nueva VENTA detectada: {symbol} @ {price_last_closed:.4f} (Zona: {short_zone})"

                active_trades[symbol] = new_trade_data
                save_active_trades(active_trades)
                enviar_telegram(mensaje)
                logger.info(log_msg) # ### CAMBIO: Usar logger.info

        except Exception as e:
             logger.error(f"Error procesando señal para {symbol}: {e}\n{traceback.format_exc()}") # ### CAMBIO: Usar logger.error con traceback

# ==============================================================================
# 6. 🔄 BUCLE PRINCIPAL
# ==============================================================================

def iniciar_monitoreo():
    logger.info("--- INICIANDO MONITOREO ---") # ### CAMBIO: Usar logger.info
    while True:
        tiempo_inicio = time.time()
        logger.info(f"--- Iniciando nuevo ciclo de monitoreo ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}) ---") # ### CAMBIO: Usar logger.info

        pivots_ok = verificar_y_actualizar_pivotes()

        if not pivots_ok:
            logger.warning("Fallo en la actualización de Pivotes. Reintentando en el próximo ciclo...") # ### CAMBIO: Usar logger.warning
            all_pivots = {}
        else:
            try:
                if os.path.exists(PIVOTS_FILE) and os.path.getsize(PIVOTS_FILE) > 0:
                    with open(PIVOTS_FILE, 'r') as f:
                        try: all_pivots = json.load(f)
                        except json.JSONDecodeError:
                             logger.warning(f"Error al decodificar {PIVOTS_FILE}. Usando pivotes vacíos.") # ### CAMBIO: Usar logger.warning
                             all_pivots = {}
                else:
                    logger.warning(f"Archivo {PIVOTS_FILE} no encontrado o vacío después de verificar. Usando pivotes vacíos.") # ### CAMBIO: Usar logger.warning
                    all_pivots = {}
            except Exception as e:
                 logger.error(f"Error cargando pivotes: {e}. Usando pivotes vacíos.") # ### CAMBIO: Usar logger.error
                 all_pivots = {}

        if all_pivots:
            logger.info("Buscando señales y chequeando trades activos...") # ### CAMBIO: Usar logger.info
            try:
                check_active_trades(all_pivots)
                detect_new_signals(all_pivots)
                logger.info("Búsqueda/Chequeo completado.") # ### CAMBIO: Usar logger.info
            except Exception as e:
                 logger.error(f"Error durante búsqueda/chequeo: {e}\n{traceback.format_exc()}") # ### CAMBIO: Usar logger.error con traceback
        else:
            logger.warning("No hay pivotes cargados para buscar señales.") # ### CAMBIO: Usar logger.warning


        duracion = time.time() - tiempo_inicio
        tiempo_espera = INTERVALO_MONITOREO_SEG - duracion

        logger.info(f"Ciclo completado en {duracion:.1f} segundos.") # ### CAMBIO: Usar logger.info
        if tiempo_espera > 0:
            logger.info(f"Esperando {int(tiempo_espera)} segundos hasta el próximo ciclo...") # ### CAMBIO: Usar logger.info
            time.sleep(max(0, tiempo_espera))
        else:
            logger.warning("El ciclo tardó más de 15 minutos.") # ### CAMBIO: Usar logger.warning

if __name__ == '__main__':
    try:
        iniciar_monitoreo()
    except KeyboardInterrupt:
        logger.info("Monitoreo detenido por el usuario (Ctrl+C).") # ### CAMBIO: Usar logger.info
    except Exception as e:
        logger.critical(f"ERROR FATAL en el bucle principal: {e}\n{traceback.format_exc()}") # ### CAMBIO: Usar logger.critical con traceback
        enviar_telegram(f"💥 BOT DETENIDO: Error fatal - {e}")
