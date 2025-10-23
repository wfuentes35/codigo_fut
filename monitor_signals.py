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
    try: requests.post(url, data=payload, timeout=10) # Añadir timeout a request
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
            # Reordenar columnas incluyendo las nuevas "out-of-the-box"
            column_order = [
                'status', 'entry_type', 'symbol', 'entry_date', 'close_date',
                'entry_price', 'close_price', 'tp1_hit', 'tp2_hit',
                'rsi_entry', 'macd_hist_entry', 'adx_entry', 'plus_di_entry', 'minus_di_entry',
                'ema_8_below_24_entry', 'short_entry_zone',
                'efficiency_ratio_entry', 'h1_trend_aligned_entry', # <-- Nuevas columnas
                'vol_ratio_entry', 'vol_pct_change_entry', 'ema_100_context', 'ema_200_context',
                'bb_upper_entry', 'bb_lower_entry',
                'tp1_key', 'tp2_key', 'sl_key'
            ]
            df_columns = [col for col in column_order if col in df.columns]
            df = df[df_columns]
            df.to_csv(HISTORICO_CSV_FILE, index=False, mode='w')
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
    except FileNotFoundError: print(f"❌ Error: Archivo {SYMBOLS_FILE} no encontrado."); return False

    all_closed_trades = load_closed_trades()
    yesterday_utc_str = (datetime.now(timezone.utc) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    trades_de_ayer = [t for t in all_closed_trades if t.get('close_date', '').startswith(yesterday_utc_str)]

    if trades_de_ayer:
        ganadoras = sum(1 for t in trades_de_ayer if t['status'] == 'CLOSED_TP')
        perdedoras = sum(1 for t in trades_de_ayer if t['status'] == 'CLOSED_SL')
        mensaje = (f"📊 **RESUMEN ({yesterday_utc_str})** 📊\n"
                   f"G: {ganadoras} | P: {perdedoras} | T: {len(trades_de_ayer)}")
        enviar_telegram(mensaje)

    all_pivots = {}
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"\n--- ⏳ Cálculo Pivotes ({today_utc}) ---")
    symbols_processed = 0
    for symbol in symbols:
        try:
            klines_daily = client.futures_historical_klines(symbol, Client.KLINE_INTERVAL_1DAY, "2 day ago", limit=2)
            if len(klines_daily) < 2: continue
            high_d, low_d, close_d = [float(klines_daily[-2][i]) for i in [2, 3, 4]]
            pivotes = calculate_pivotes_fibonacci(high_d, low_d, close_d)
            all_pivots[symbol] = {'date': today_utc, 'levels': pivotes}
            symbols_processed += 1
            if symbols_processed % 50 == 0: print(f"   ...{symbols_processed}/{len(symbols)}") # Progreso cada 50
        except Exception as e: print(f"   ❌ Error Pivotes {symbol}: {e}")

    with open(PIVOTS_FILE, 'w') as f: json.dump(all_pivots, f, indent=4)
    print(f"--- ✅ {len(all_pivots)} Pivotes guardados ---")
    enviar_telegram(f"⭐️ **PIVOTES ACTUALIZADOS** {today_utc} ({len(all_pivots)} pares).")
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
    trades_closed_in_cycle = False

    for symbol, trade in active_trades.items():
        if trade.get('status') != 'OPEN': continue
        try:
            klines_15m = client.futures_historical_klines(symbol, Client.KLINE_INTERVAL_15MINUTE, "15m ago", limit=1)
            if not klines_15m: continue
            price = float(klines_15m[-1][4])
            pivotes = all_pivots.get(symbol, {}).get('levels', {})
            if not pivotes or not all(k in pivotes for k in [trade['tp1_key'], trade['tp2_key'], trade['sl_key']]):
                 print(f"⚠️ Faltan pivotes {symbol}."); continue

            tp1_level, tp2_level, sl_level = pivotes[trade['tp1_key']], pivotes[trade['tp2_key']], pivotes[trade['sl_key']]
            is_long = trade['entry_type'] == 'LONG'

            # SL Check
            if (is_long and price < sl_level) or (not is_long and price > sl_level):
                mensaje = f"🛑 *SL {trade['entry_type']} {symbol}* | P: {price:.4f} SL: {sl_level:.4f}"
                enviar_telegram(mensaje)
                trade.update({'status': 'CLOSED_SL', 'close_price': price, 'close_date': datetime.now().isoformat(), 'symbol': symbol})
                closed_trades_list.append(trade); del updated_trades[symbol]; trades_closed_in_cycle = True; continue

            # TP2 Check
            if (is_long and price > tp2_level) or (not is_long and price < tp2_level):
                if not trade.get('tp1_hit'): updated_trades[symbol]['tp1_hit'] = True
                mensaje = f"🎯 *TP2 {trade['entry_type']} {symbol}* | P: {price:.4f} TP2: {tp2_level:.4f}"
                enviar_telegram(mensaje)
                trade.update({'status': 'CLOSED_TP', 'tp2_hit': True, 'close_price': price, 'close_date': datetime.now().isoformat(), 'symbol': symbol})
                closed_trades_list.append(trade); del updated_trades[symbol]; trades_closed_in_cycle = True; continue

            # TP1 Check
            if (is_long and price > tp1_level) or (not is_long and price < tp1_level):
                if not trade.get('tp1_hit'):
                    mensaje = f"✅ *TP1 {trade['entry_type']} {symbol}* | P: {price:.4f} TP1: {tp1_level:.4f}"
                    enviar_telegram(mensaje); updated_trades[symbol]['tp1_hit'] = True
        except Exception as e: print(f"❌ Error check {symbol}: {e}")

    save_active_trades(updated_trades)
    if trades_closed_in_cycle:
        current_closed = load_closed_trades()
        existing = {(t.get('symbol'), t.get('entry_date')) for t in current_closed}
        newly_closed = [t for t in closed_trades_list if (t.get('symbol'), t.get('entry_date')) not in existing and t.get('status','').startswith('CLOSED')]
        current_closed.extend(newly_closed)
        save_closed_trades(current_closed)


# ==============================================================================
# 5. 🚦 DETECCIÓN DE NUEVAS SEÑALES (CON NUEVOS DATOS OBSERVACIONALES)
# ==============================================================================

def calculate_adx(df, period=14):
    df['Prev_Close'] = df['Close'].shift(1); df['Prev_High'] = df['High'].shift(1); df['Prev_Low'] = df['Low'].shift(1)
    df['TR'] = pd.DataFrame({'hl': df['High'] - df['Low'], 'hc': abs(df['High'] - df['Prev_Close']), 'lc': abs(df['Low'] - df['Prev_Close'])}).max(axis=1)
    move_up = df['High'] - df['Prev_High']; move_down = df['Prev_Low'] - df['Low']
    df['+DM'] = np.where((move_up > move_down) & (move_up > 0), move_up, 0)
    df['-DM'] = np.where((move_down > move_up) & (move_down > 0), move_down, 0)
    TR_smooth = df['TR'].ewm(span=period, adjust=False).mean()
    DM_plus_smooth = df['+DM'].ewm(span=period, adjust=False).mean(); DM_minus_smooth = df['-DM'].ewm(span=period, adjust=False).mean()
    df['DI_plus'] = np.where(TR_smooth != 0, (DM_plus_smooth / TR_smooth) * 100, 0)
    df['DI_minus'] = np.where(TR_smooth != 0, (DM_minus_smooth / TR_smooth) * 100, 0)
    DI_diff = abs(df['DI_plus'] - df['DI_minus']); DI_sum = df['DI_plus'] + df['DI_minus']
    df['DX'] = np.where(DI_sum != 0, (DI_diff / DI_sum) * 100, 0)
    df['ADX'] = df['DX'].ewm(span=period, adjust=False).mean()
    return df

def calculate_efficiency_ratio(series, period):
    """Calcula el Ratio de Eficiencia."""
    if len(series) < period + 1: return np.nan
    change = abs(series.iloc[-1] - series.iloc[-period-1])
    volatility = abs(series.diff()).iloc[-period:].sum()
    return change / volatility if volatility != 0 else 0

def get_h1_trend_alignment(symbol, entry_type):
    """Verifica si la tendencia H1 se alinea con la señal M15."""
    try:
        # Pedir suficientes velas H1 para calcular EMA50 y MACD
        klines_h1 = client.futures_historical_klines(symbol, Client.KLINE_INTERVAL_1HOUR, "5 day ago UTC", limit=100)
        if len(klines_h1) < 51: return None # Necesitamos al menos 51 para EMA50

        df_h1 = pd.DataFrame(klines_h1, columns=['open_time', 'Open', 'High', 'Low', 'Close', 'Volume', 'ct', 'qav','nt','tbbav','tbqav','ig'])
        df_h1['Close'] = df_h1['Close'].astype(float)

        # Calcular EMA50 en H1
        df_h1['EMA50_H1'] = df_h1['Close'].ewm(span=50, adjust=False).mean()

        # Calcular MACD en H1 (simplificado, solo el histograma)
        ema12_h1 = df_h1['Close'].ewm(span=12, adjust=False).mean()
        ema26_h1 = df_h1['Close'].ewm(span=26, adjust=False).mean()
        macd_line_h1 = ema12_h1 - ema26_h1
        macd_signal_h1 = macd_line_h1.ewm(span=9, adjust=False).mean()
        macd_hist_h1 = macd_line_h1 - macd_signal_h1

        last_h1 = df_h1.iloc[-1]

        # Verificar alineación
        if entry_type == 'LONG':
            return last_h1['Close'] > last_h1['EMA50_H1'] and macd_hist_h1.iloc[-1] > 0
        elif entry_type == 'SHORT':
            return last_h1['Close'] < last_h1['EMA50_H1'] and macd_hist_h1.iloc[-1] < 0
        else:
            return None
    except Exception as e:
        print(f"   ⚠️ Error H1 {symbol}: {e}")
        return None


def detect_new_signals(all_pivots):
    active_trades = load_active_trades()
    for symbol, pivot_data in all_pivots.items():
        if symbol in active_trades: continue
        try:
            pivotes = pivot_data['levels']
            if not all(k in pivotes for k in ['R1', 'R2', 'R3', 'S1', 'PP']): continue
            R1, R2, R3, S1, PP = pivotes['R1'], pivotes['R2'], pivotes['R3'], pivotes['S1'], pivotes['PP']

            klines_15m = client.futures_historical_klines(symbol, Client.KLINE_INTERVAL_15MINUTE, "55 hour ago", limit=250)
            if len(klines_15m) < 201: continue

            df = pd.DataFrame(klines_15m, columns=['ot', 'O', 'H', 'L', 'C', 'V', 'ct', 'qav','nt','tbbav','tbqav','ig'])
            df.rename(columns={'C': 'Close', 'V': 'Volume', 'H': 'High', 'L': 'Low'}, inplace=True) # Renombrar columnas
            df[['Close', 'Volume', 'High', 'Low']] = df[['Close', 'Volume', 'High', 'Low']].astype(float)


            # --- CÁLCULO DE INDICADORES M15 ---
            df['EMA8'] = df['Close'].ewm(span=8, adjust=False).mean()
            df['EMA24'] = df['Close'].ewm(span=24, adjust=False).mean()
            df['EMA50'] = df['Close'].ewm(span=50, adjust=False).mean()
            df['EMA100'] = df['Close'].ewm(span=100, adjust=False).mean()
            df['EMA200'] = df['Close'].ewm(span=200, adjust=False).mean()
            delta = df['Close'].diff(); gain = (delta.where(delta > 0, 0)).rolling(window=14).mean(); loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            df['RSI'] = 100 - (100 / (1 + (gain / loss)))
            df['EMA12'] = df['Close'].ewm(span=12, adjust=False).mean(); df['EMA26'] = df['Close'].ewm(span=26, adjust=False).mean()
            df['MACD_hist'] = (df['EMA12'] - df['EMA26']) - (df['EMA12'] - df['EMA26']).ewm(span=9, adjust=False).mean()
            df['BB_middle'] = df['Close'].rolling(window=20).mean(); std_dev = df['Close'].rolling(window=20).std()
            df['BB_upper'] = df['BB_middle'] + (std_dev * 2); df['BB_lower'] = df['BB_middle'] - (std_dev * 2)
            df['Volume_MA20'] = df['Volume'].rolling(window=20).mean()
            df = calculate_adx(df, period=14)
            df['Efficiency_Ratio'] = df['Close'].rolling(window=EFFICIENCY_RATIO_PERIOD + 1).apply(lambda x: calculate_efficiency_ratio(x, EFFICIENCY_RATIO_PERIOD))

            # --- DATOS DE LA ÚLTIMA VELA ---
            last, prev = df.iloc[-1], df.iloc[-2]
            if pd.isna(last[['ADX', 'RSI', 'MACD_hist', 'BB_upper', 'Efficiency_Ratio']]).any(): continue

            price_last_closed = last['Close']; bb_upper_actual = last['BB_upper']; adx_actual = last['ADX']
            rsi_actual = last['RSI']; macd_hist_actual = last['MACD_hist']; ema8_below_24 = last['EMA8'] < last['EMA24']
            efficiency_ratio_actual = last['Efficiency_Ratio']

            cruce_alcista = (prev['EMA24'] < prev['EMA50']) and (last['EMA24'] > last['EMA50'])
            cruce_bajista = (prev['EMA24'] > prev['EMA50']) and (last['EMA24'] < last['EMA50'])

            # --- LÓGICA DE ENTRADA CON TODOS LOS FILTROS Y DATOS OBSERVACIONALES ---

            entry_signal = None # Para almacenar el tipo de señal detectada
            entry_type = None

            # CONDICIONES PARA COMPRA (LONG)
            if (cruce_alcista and (S1 < price_last_closed < R1) and macd_hist_actual > 0 and
                rsi_actual < 75 and adx_actual > 25 and price_last_closed < bb_upper_actual):
                entry_signal = True
                entry_type = 'LONG'

            # CONDICIONES PARA VENTA (SHORT)
            elif (cruce_bajista and (PP < price_last_closed < R3) and macd_hist_actual < 0 and
                  rsi_actual > 35 and adx_actual > 25 and ema8_below_24):
                entry_signal = True
                entry_type = 'SHORT'

            # Si se detectó una señal válida, obtener datos adicionales y guardar
            if entry_signal:
                 # Obtener alineación H1
                h1_aligned = get_h1_trend_alignment(symbol, entry_type)

                vol_hora_ant = df['Volume'].iloc[-5:-1].mean()
                vol_pct_change = ((last['Volume'] - vol_hora_ant) / vol_hora_ant) * 100 if vol_hora_ant > 0 else 0
                vol_ratio = last['Volume'] / last['Volume_MA20'] if last['Volume_MA20'] > 0 else 0

                short_zone = None
                if entry_type == 'SHORT':
                    if price_last_closed > R2: short_zone = "Above R2"
                    elif price_last_closed > R1: short_zone = "Above R1"
                    elif price_last_closed > PP: short_zone = "Above PP"

                new_trade_data = {
                    'status': 'OPEN', 'entry_price': price_last_closed,
                    'tp1_hit': False, 'tp2_hit': False, 'entry_date': datetime.now().isoformat(),
                    'vol_pct_change_entry': round(vol_pct_change, 2),
                    'ema_100_context': price_last_closed > last['EMA100'],
                    'ema_200_context': price_last_closed > last['EMA200'],
                    'vol_ratio_entry': round(vol_ratio, 2),
                    'rsi_entry': round(rsi_actual, 2),
                    'macd_hist_entry': round(macd_hist_actual, 6),
                    'bb_upper_entry': round(bb_upper_actual, 4),
                    'bb_lower_entry': round(last['BB_lower'], 4),
                    'adx_entry': round(adx_actual, 2),
                    'plus_di_entry': round(last['DI_plus'], 2),
                    'minus_di_entry': round(last['DI_minus'], 2),
                    'ema_8_below_24_entry': ema8_below_24,
                    'short_entry_zone': short_zone,
                    'efficiency_ratio_entry': round(efficiency_ratio_actual, 3), # <-- Dato nuevo
                    'h1_trend_aligned_entry': h1_aligned, # <-- Dato nuevo
                    'entry_type': entry_type
                }

                if entry_type == 'LONG':
                    new_trade_data.update({'tp1_key': 'R1', 'tp2_key': 'R2', 'sl_key': 'S1'})
                    mensaje = (f"🚀 *Compra {symbol}* | P:{price_last_closed:.4f} RSI:{rsi_actual:.1f} ADX:{adx_actual:.1f}")
                    print_msg = f"✅ Compra {symbol}"
                else: # SHORT
                    new_trade_data.update({'tp1_key': 'PP', 'tp2_key': 'S1', 'sl_key': 'R2'})
                    mensaje = (f"🔻 *Venta {symbol}* | P:{price_last_closed:.4f} RSI:{rsi_actual:.1f} ADX:{adx_actual:.1f} Z:{short_zone}")
                    print_msg = f"✅ Venta {symbol} (Z:{short_zone})"

                active_trades[symbol] = new_trade_data
                save_active_trades(active_trades)
                enviar_telegram(mensaje)
                print(print_msg)

        except Exception as e: print(f"❌ Error señal {symbol}: {e}")

# ==============================================================================
# 6. 🔄 BUCLE PRINCIPAL
# ==============================================================================

def iniciar_monitoreo():
    print("--- 🤖 Iniciando monitoreo ---")
    while True:
        tiempo_inicio = time.time()
        print(f"\n--- Ciclo {datetime.now().strftime('%H:%M:%S')} ---") # Hora inicio ciclo

        if not verificar_y_actualizar_pivotes():
            print("🛑 Fallo Pivotes. Reintentando...")

        try:
            with open(PIVOTS_FILE, 'r') as f: all_pivots = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError): all_pivots = {}

        if all_pivots:
            print("   🔍 Buscando señales...")
            check_active_trades(all_pivots)
            detect_new_signals(all_pivots)
            print("   ✅ Búsqueda completa.")
        else:
            print("   ⚠️ No hay pivotes cargados.")

        duracion = time.time() - tiempo_inicio
        tiempo_espera = INTERVALO_MONITOREO_SEG - duracion

        print(f"   ⏱️ Ciclo {duracion:.1f}s.")
        if tiempo_espera > 0:
            print(f"   💤 Esperando {int(tiempo_espera)}s...")
            time.sleep(tiempo_espera)
        else:
            print("   ⚠️ Ciclo > 15 min.")

if __name__ == '__main__':
    iniciar_monitoreo()

