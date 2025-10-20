import pandas as pd
import numpy as np
from binance.client import Client
import json
import time
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv
import os

# Cargar variables de entorno del archivo .env
load_dotenv()

# ==============================================================================
# 1. ⚙️ CONFIGURACIÓN Y ESTADO GLOBAL
# ==============================================================================
API_KEY = os.getenv("API_KEY")
SECRET_KEY = os.getenv("SECRET_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not API_KEY or not SECRET_KEY:
    raise ValueError("ERROR: Las claves API_KEY o SECRET_KEY no se encontraron en el archivo .env.")
client = Client(API_KEY, SECRET_KEY)

# Nombres de archivos
SYMBOLS_FILE = 'top_100_symbols.json'
PIVOTS_FILE = 'daily_pivots.json'
TRADES_FILE = 'active_trades.json'
CLOSED_TRADES_FILE = 'closed_trades.json'
HISTORICO_CSV_FILE = 'historico_trades.csv'

INTERVALO_MONITOREO_SEG = 900 # 15 minutos

# ==============================================================================
# 2. 🧮 FÓRMULAS Y UTILIDADES
# ==============================================================================

def calcular_pivotes_fibonacci(high, low, close):
    rango = high - low
    PP = (high + low + close) / 3
    FIB_382, FIB_618, FIB_100 = 0.382, 0.618, 1.000
    R1 = PP + (rango * FIB_382)
    R2 = PP + (rango * FIB_618)
    R3 = PP + (rango * FIB_100)
    S1 = PP - (rango * FIB_382)
    S2 = PP - (rango * FIB_618)
    S3 = PP - (rango * FIB_100)
    return {k: round(v, 4) for k, v in {'PP': PP, 'R1': R1, 'R2': R2, 'R3': R3, 'S1': S1, 'S2': S2, 'S3': S3}.items()}

def enviar_telegram(mensaje):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': mensaje, 'parse_mode': 'Markdown'}
    try: requests.post(url, data=payload)
    except Exception as e: print(f"❌ Error al enviar mensaje a Telegram: {e}")

# Funciones de persistencia de estado de operaciones

def load_active_trades():
    try:
        with open(TRADES_FILE, 'r') as f: return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError): return {}

def save_active_trades(trades):
    def enhanced_json_converter(obj):
        if isinstance(obj, np.integer): return int(obj)
        if isinstance(obj, np.floating): return float(obj)
        if isinstance(obj, np.bool_): return bool(obj)
        if isinstance(obj, datetime): return obj.isoformat()
        raise TypeError(f'Object of type {obj.__class__.__name__} is not JSON serializable')
    try:
        with open(TRADES_FILE, 'w') as f: json.dump(trades, f, indent=4, default=enhanced_json_converter)
    except Exception as e: print(f"❌ Error al guardar {TRADES_FILE}: {e}")

def load_closed_trades():
    try:
        with open(CLOSED_TRADES_FILE, 'r') as f: return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError): return []

def save_closed_trades(trades_list):
    """Guarda el historial en JSON y también en un archivo CSV para análisis en Excel."""
    try:
        with open(CLOSED_TRADES_FILE, 'w') as f: json.dump(trades_list, f, indent=4, default=str)
    except Exception as e: print(f"❌ Error al guardar {CLOSED_TRADES_FILE}: {e}")

    if trades_list:
        try:
            df = pd.DataFrame(trades_list)
            # Reordenar columnas para mejor legibilidad en el CSV
            column_order = [
                'status', 'entry_type', 'symbol', 'entry_date', 'close_date',
                'entry_price', 'close_price', 'tp1_hit', 'tp2_hit',
                'rsi_entry', 'macd_hist_entry', 'adx_entry', 'plus_di_entry', 'minus_di_entry',
                'ema_8_below_24_entry', # Añadida columna EMA 8
                'vol_ratio_entry', 'vol_pct_change_entry', 'ema_100_context', 'ema_200_context',
                'bb_upper_entry', 'bb_lower_entry',
                'tp1_key', 'tp2_key', 'sl_key'
            ]
            # Filtrar para solo incluir columnas que existen en el df
            df_columns = [col for col in column_order if col in df.columns]
            df = df[df_columns]
            df.to_csv(HISTORICO_CSV_FILE, index=False)
            print(f"✅ Historial actualizado en {HISTORICO_CSV_FILE}")
        except Exception as e:
            print(f"❌ Error al guardar historial en CSV: {e}")

# ==============================================================================
# 3. 💾 FUNCIÓN DE ACTUALIZACIÓN DIARIA DE PIVOTES Y RESUMEN
# ==============================================================================

def actualizar_pivotes_diarios():
    """Calcula Pivotes y envía resumen diario sin borrar el historial."""
    try:
        with open(SYMBOLS_FILE, 'r') as f: symbols = json.load(f)
    except FileNotFoundError:
        print(f"❌ Error: Archivo {SYMBOLS_FILE} no encontrado."); return False

    all_closed_trades = load_closed_trades()
    yesterday_utc_str = (datetime.now(timezone.utc) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    trades_de_ayer = [t for t in all_closed_trades if t.get('close_date', '').startswith(yesterday_utc_str)]

    if trades_de_ayer:
        ganadoras = sum(1 for t in trades_de_ayer if t['status'] == 'CLOSED_TP')
        perdedoras = sum(1 for t in trades_de_ayer if t['status'] == 'CLOSED_SL')
        mensaje_resumen = (f"📊 **RESUMEN DEL DÍA ANTERIOR ({yesterday_utc_str})** 📊\n"
                           f"Ganadoras (TP): {ganadoras}\n"
                           f"Perdedoras (SL): {perdedoras}\n"
                           f"Total: {len(trades_de_ayer)}")
        enviar_telegram(mensaje_resumen)

    all_pivots = {}
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"\n--- ⏳ INICIANDO CÁLCULO DIARIO DE PIVOTES ({today_utc}) ---")

    for i, symbol in enumerate(symbols):
        try:
            klines_daily = client.futures_historical_klines(symbol, Client.KLINE_INTERVAL_1DAY, "2 day ago", limit=2)
            if len(klines_daily) < 2: continue
            high_d, low_d, close_d = [float(klines_daily[-2][i]) for i in [2, 3, 4]]
            pivotes = calcular_pivotes_fibonacci(high_d, low_d, close_d)
            all_pivots[symbol] = {'date': today_utc, 'levels': pivotes}
            if i % 20 == 0: print(f"   Calculando... {symbol}")
        except Exception as e: print(f"   ❌ Error al calcular Pivotes para {symbol}: {e}")

    with open(PIVOTS_FILE, 'w') as f: json.dump(all_pivots, f, indent=4)
    print(f"--- ✅ {len(all_pivots)} Pivotes guardados en {PIVOTS_FILE} ---")
    enviar_telegram(f"⭐️ **PIVOTES ACTUALIZADOS** para el día {today_utc}.")
    return True

def verificar_y_actualizar_pivotes():
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        with open(PIVOTS_FILE, 'r') as f: daily_data = json.load(f)
        if daily_data and any(data.get('date') == today_utc for data in daily_data.values()): return True
        else: return actualizar_pivotes_diarios()
    except (FileNotFoundError, json.JSONDecodeError): return actualizar_pivotes_diarios()

# ==============================================================================
# 4. 📈 LÓGICA DE SEGUIMIENTO DE OPERACIONES (TP/SL)
# ==============================================================================

def check_active_trades(all_pivots):
    active_trades = load_active_trades()
    if not active_trades: return

    updated_trades = active_trades.copy()
    closed_trades_list = load_closed_trades()
    trades_closed_in_cycle = False # Flag para saber si se cerró algún trade

    for symbol, trade in active_trades.items():
        if trade.get('status') != 'OPEN': continue
        try:
            klines_15m = client.futures_historical_klines(symbol, Client.KLINE_INTERVAL_15MINUTE, "15m ago", limit=1)
            if not klines_15m: continue
            price = float(klines_15m[-1][4])
            pivotes = all_pivots.get(symbol, {}).get('levels', {})
            if not pivotes: continue
            if trade['tp1_key'] not in pivotes or trade['tp2_key'] not in pivotes or trade['sl_key'] not in pivotes:
                print(f"⚠️ Advertencia: Faltan claves de pivote para {symbol}. Saltando trade.")
                continue

            tp1_level, tp2_level, sl_level = pivotes[trade['tp1_key']], pivotes[trade['tp2_key']], pivotes[trade['sl_key']]
            is_long = trade['entry_type'] == 'LONG'

            # Comprobar SL
            if (is_long and price < sl_level) or (not is_long and price > sl_level):
                mensaje = f"🛑 *SL ALCANZADO {trade['entry_type']} EN {symbol}* 🛑\nPrecio: {price:.4f} | Nivel SL: {sl_level:.4f}"
                enviar_telegram(mensaje)
                trade.update({'status': 'CLOSED_SL', 'close_price': price, 'close_date': datetime.now().isoformat(), 'symbol': symbol})
                closed_trades_list.append(trade)
                del updated_trades[symbol]
                trades_closed_in_cycle = True
                continue

            # Comprobar TP2
            if (is_long and price > tp2_level) or (not is_long and price < tp2_level):
                if not trade.get('tp1_hit'): updated_trades[symbol]['tp1_hit'] = True
                mensaje = f"🎯 *TP2 LOGRADO {trade['entry_type']} EN {symbol}* 🎯\nPrecio: {price:.4f} | Nivel TP2: {tp2_level:.4f}"
                enviar_telegram(mensaje)
                trade.update({'status': 'CLOSED_TP', 'tp2_hit': True, 'close_price': price, 'close_date': datetime.now().isoformat(), 'symbol': symbol})
                closed_trades_list.append(trade)
                del updated_trades[symbol]
                trades_closed_in_cycle = True
                continue

            # Comprobar TP1
            if (is_long and price > tp1_level) or (not is_long and price < tp1_level):
                if not trade.get('tp1_hit'):
                    mensaje = f"✅ *TP1 LOGRADO {trade['entry_type']} EN {symbol}* ✅\nPrecio: {price:.4f} | Nivel TP1: {tp1_level:.4f}"
                    enviar_telegram(mensaje)
                    updated_trades[symbol]['tp1_hit'] = True
        except Exception as e: print(f"❌ Error al chequear trade activo para {symbol}: {e}")

    save_active_trades(updated_trades)
    # Guardar en JSON y CSV solo si se cerró algún trade en este ciclo
    if trades_closed_in_cycle:
        save_closed_trades(closed_trades_list)


# ==============================================================================
# 5. 🚦 DETECCIÓN DE NUEVAS SEÑALES (CON EMA 8 Y ADX AÑADIDOS)
# ==============================================================================

def calculate_adx(df, period=14):
    """Calcula el ADX, +DI y -DI."""
    df['Prev_Close'] = df['Close'].shift(1)
    df['Prev_High'] = df['High'].shift(1)
    df['Prev_Low'] = df['Low'].shift(1)

    df['High-Low'] = df['High'] - df['Low']
    df['High-PrevClose'] = abs(df['High'] - df['Prev_Close'])
    df['Low-PrevClose'] = abs(df['Low'] - df['Prev_Close'])

    df['TR'] = df[['High-Low', 'High-PrevClose', 'Low-PrevClose']].max(axis=1)

    move_up = df['High'] - df['Prev_High']
    move_down = df['Prev_Low'] - df['Low']

    df['+DM'] = np.where((move_up > move_down) & (move_up > 0), move_up, 0)
    df['-DM'] = np.where((move_down > move_up) & (move_down > 0), move_down, 0)

    # Suavizado con EWM (similar a Wilder's)
    TR_smooth = df['TR'].ewm(span=period, adjust=False).mean()
    DM_plus_smooth = df['+DM'].ewm(span=period, adjust=False).mean()
    DM_minus_smooth = df['-DM'].ewm(span=period, adjust=False).mean()

    df['DI_plus'] = np.where(TR_smooth > 0, (DM_plus_smooth / TR_smooth) * 100, 0)
    df['DI_minus'] = np.where(TR_smooth > 0, (DM_minus_smooth / TR_smooth) * 100, 0)

    DI_diff = abs(df['DI_plus'] - df['DI_minus'])
    DI_sum = df['DI_plus'] + df['DI_minus']

    df['DX'] = np.where(DI_sum > 0, (DI_diff / DI_sum) * 100, 0)
    df['ADX'] = df['DX'].ewm(span=period, adjust=False).mean()

    return df

def detect_new_signals(all_pivots):
    active_trades = load_active_trades()
    for symbol, pivot_data in all_pivots.items():
        try:
            if symbol in active_trades: continue
            pivotes = pivot_data['levels']
            if not all(k in pivotes for k in ['R1', 'R2', 'R3', 'S1', 'PP']): continue # Asegura que todos los pivotes necesarios existan
            R1, R2, R3, S1, PP = pivotes['R1'], pivotes['R2'], pivotes['R3'], pivotes['S1'], pivotes['PP']

            klines_15m = client.futures_historical_klines(symbol, Client.KLINE_INTERVAL_15MINUTE, "55 hour ago", limit=250)
            if len(klines_15m) < 201: continue

            df = pd.DataFrame(klines_15m, columns=['open_time', 'Open', 'High', 'Low', 'Close', 'Volume', 'close_time', 'qav', 'trades', 'tbav', 'tqav', 'ignore'])
            df[['Close', 'Volume', 'High', 'Low']] = df[['Close', 'Volume', 'High', 'Low']].astype(float)

            # --- CÁLCULO DE INDICADORES ---
            df['EMA8'] = df['Close'].ewm(span=8, adjust=False).mean() # <-- EMA 8 AÑADIDA
            df['EMA24'] = df['Close'].ewm(span=24, adjust=False).mean()
            df['EMA50'] = df['Close'].ewm(span=50, adjust=False).mean()
            df['EMA100'] = df['Close'].ewm(span=100, adjust=False).mean()
            df['EMA200'] = df['Close'].ewm(span=200, adjust=False).mean()

            delta = df['Close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            df['RSI'] = 100 - (100 / (1 + (gain / loss)))

            df['EMA12'] = df['Close'].ewm(span=12, adjust=False).mean()
            df['EMA26'] = df['Close'].ewm(span=26, adjust=False).mean()
            df['MACD_hist'] = (df['EMA12'] - df['EMA26']) - (df['EMA12'] - df['EMA26']).ewm(span=9, adjust=False).mean()

            df['BB_middle'] = df['Close'].rolling(window=20).mean()
            std_dev = df['Close'].rolling(window=20).std()
            df['BB_upper'] = df['BB_middle'] + (std_dev * 2)
            df['BB_lower'] = df['BB_middle'] - (std_dev * 2)
            df['Volume_MA20'] = df['Volume'].rolling(window=20).mean()

            df = calculate_adx(df, period=14)

            # --- DATOS DE LA ÚLTIMA VELA ---
            last, prev = df.iloc[-1], df.iloc[-2]
            price_last_closed = last['Close']

            cruce_alcista = (prev['EMA24'] < prev['EMA50']) and (last['EMA24'] > last['EMA50'])
            cruce_bajista = (prev['EMA24'] > prev['EMA50']) and (last['EMA24'] < last['EMA50'])

            rsi_actual = last['RSI']
            macd_hist_actual = last['MACD_hist']
            # Condición EMA 8 vs EMA 24
            ema8_below_24 = last['EMA8'] < last['EMA24']

            # --- LÓGICA DE ENTRADA CON FILTROS ---

            # CONDICIONES PARA COMPRA (LONG)
            if (cruce_alcista and
                (S1 < price_last_closed < R1) and
                macd_hist_actual > 0 and
                rsi_actual < 75):

                vol_hora_ant = df['Volume'].iloc[-5:-1].mean()
                vol_pct_change = ((last['Volume'] - vol_hora_ant) / vol_hora_ant) * 100 if vol_hora_ant > 0 else 0
                vol_ratio = last['Volume'] / last['Volume_MA20'] if last['Volume_MA20'] > 0 else 0

                new_trade_data = {
                    'status': 'OPEN', 'entry_price': price_last_closed,
                    'tp1_hit': False, 'tp2_hit': False, 'entry_date': datetime.now().isoformat(),
                    'vol_pct_change_entry': round(vol_pct_change, 2),
                    'ema_100_context': price_last_closed > last['EMA100'],
                    'ema_200_context': price_last_closed > last['EMA200'],
                    'vol_ratio_entry': round(vol_ratio, 2),
                    'rsi_entry': round(rsi_actual, 2) if pd.notna(rsi_actual) else None,
                    'macd_hist_entry': round(macd_hist_actual, 6) if pd.notna(macd_hist_actual) else None,
                    'bb_upper_entry': round(last['BB_upper'], 4) if pd.notna(last['BB_upper']) else None,
                    'bb_lower_entry': round(last['BB_lower'], 4) if pd.notna(last['BB_lower']) else None,
                    'adx_entry': round(last['ADX'], 2) if pd.notna(last['ADX']) else None,
                    'plus_di_entry': round(last['DI_plus'], 2) if pd.notna(last['DI_plus']) else None,
                    'minus_di_entry': round(last['DI_minus'], 2) if pd.notna(last['DI_minus']) else None,
                    'ema_8_below_24_entry': ema8_below_24, # Guardar estado EMA 8
                    'entry_type': 'LONG', 'tp1_key': 'R1', 'tp2_key': 'R2', 'sl_key': 'S1'
                }
                active_trades[symbol] = new_trade_data
                save_active_trades(active_trades)
                mensaje = (f"🚀 *NUEVA COMPRA (Filtrada) {symbol}*\n"
                           f"Precio: {price_last_closed:.4f} | RSI: {rsi_actual:.2f}")
                enviar_telegram(mensaje)
                print(f"✅ NUEVA COMPRA (Filtrada) detectada: {symbol}")

            # CONDICIONES PARA VENTA (SHORT) - LÓGICA MEJORADA
            elif (cruce_bajista and
                  (R1 < price_last_closed < R3) and
                  macd_hist_actual < 0 and
                  rsi_actual > 35):

                vol_hora_ant = df['Volume'].iloc[-5:-1].mean()
                vol_pct_change = ((last['Volume'] - vol_hora_ant) / vol_hora_ant) * 100 if vol_hora_ant > 0 else 0
                vol_ratio = last['Volume'] / last['Volume_MA20'] if last['Volume_MA20'] > 0 else 0

                new_trade_data = {
                    'status': 'OPEN', 'entry_price': price_last_closed,
                    'tp1_hit': False, 'tp2_hit': False, 'entry_date': datetime.now().isoformat(),
                    'vol_pct_change_entry': round(vol_pct_change, 2),
                    'ema_100_context': price_last_closed > last['EMA100'],
                    'ema_200_context': price_last_closed > last['EMA200'],
                    'vol_ratio_entry': round(vol_ratio, 2),
                    'rsi_entry': round(rsi_actual, 2) if pd.notna(rsi_actual) else None,
                    'macd_hist_entry': round(macd_hist_actual, 6) if pd.notna(macd_hist_actual) else None,
                    'bb_upper_entry': round(last['BB_upper'], 4) if pd.notna(last['BB_upper']) else None,
                    'bb_lower_entry': round(last['BB_lower'], 4) if pd.notna(last['BB_lower']) else None,
                    'adx_entry': round(last['ADX'], 2) if pd.notna(last['ADX']) else None,
                    'plus_di_entry': round(last['DI_plus'], 2) if pd.notna(last['DI_plus']) else None,
                    'minus_di_entry': round(last['DI_minus'], 2) if pd.notna(last['DI_minus']) else None,
                    'ema_8_below_24_entry': ema8_below_24, # Guardar estado EMA 8
                    'entry_type': 'SHORT', 'tp1_key': 'PP', 'tp2_key': 'S1', 'sl_key': 'R2'
                }
                active_trades[symbol] = new_trade_data
                save_active_trades(active_trades)
                mensaje = (f"🔻 *NUEVA VENTA (Filtrada) {symbol}*\n"
                           f"Precio: {price_last_closed:.4f} | RSI: {rsi_actual:.2f}")
                enviar_telegram(mensaje)
                print(f"✅ NUEVA VENTA (Filtrada) detectada: {symbol}")

        except Exception as e: print(f"❌ Error al procesar {symbol} en detección de señales: {e}")

# ==============================================================================
# 6. 🔄 BUCLE PRINCIPAL
# ==============================================================================

def iniciar_monitoreo():
    print("--- 🤖 Iniciando monitoreo de señales (15m) ---")
    while True:
        tiempo_inicio = time.time()

        if not verificar_y_actualizar_pivotes():
            print("🛑 Fallo al actualizar Pivotes. Reintentando en el próximo ciclo.")

        try:
            with open(PIVOTS_FILE, 'r') as f: all_pivots = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError): all_pivots = {}

        if all_pivots:
            check_active_trades(all_pivots)
            detect_new_signals(all_pivots)

        duracion = time.time() - tiempo_inicio
        tiempo_espera = INTERVALO_MONITOREO_SEG - duracion

        print(f"Ciclo completado en {duracion:.2f} segundos.")
        if tiempo_espera > 0:
            print(f"💤 Esperando {int(tiempo_espera)} segundos hasta el próximo ciclo...")
            time.sleep(tiempo_espera)
        else:
            print("⚠️ Advertencia: El ciclo tardó más de 15 minutos.")

if __name__ == '__main__':
    iniciar_monitoreo()
