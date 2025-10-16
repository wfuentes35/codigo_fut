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
CLOSED_TRADES_FILE = 'closed_trades.json' # Archivo para histórico

INTERVALO_MONITOREO_SEG = 900 # 15 minutos

# ==============================================================================
# 2. 🧮 FÓRMULAS Y UTILIDADES
# ==============================================================================

def calcular_pivotes_fibonacci(high, low, close):
    """Calcula los niveles de Pivote Fibonacci."""
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
    """Envía un mensaje a través de la API de Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': mensaje, 'parse_mode': 'Markdown'}
    try: requests.post(url, data=payload)
    except Exception as e: print(f"❌ Error al enviar mensaje a Telegram: {e}")

# Funciones de persistencia de estado de operaciones

def load_active_trades():
    """Carga el estado de las operaciones activas."""
    try:
        with open(TRADES_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_active_trades(trades):
    """
    Guarda el estado de las operaciones activas. 
    Usa un conversor para manejar tipos de datos de NumPy, previniendo errores de serialización.
    """
    def enhanced_json_converter(obj):
        """Convierte tipos de NumPy a tipos nativos de Python para que sean serializables."""
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.bool_):
            return bool(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(f'Object of type {obj.__class__.__name__} is not JSON serializable')

    try:
        with open(TRADES_FILE, 'w') as f:
            json.dump(trades, f, indent=4, default=enhanced_json_converter)
    except Exception as e:
        print(f"❌ Error al guardar {TRADES_FILE}: {e}")

def load_closed_trades():
    """Carga el historial de operaciones cerradas."""
    try:
        with open(CLOSED_TRADES_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def save_closed_trades(trades_list):
    """Guarda el historial de operaciones cerradas."""
    try:
        with open(CLOSED_TRADES_FILE, 'w') as f:
            json.dump(trades_list, f, indent=4, default=str) # Usamos default=str como fallback para fechas
    except Exception as e:
        print(f"❌ Error al guardar {CLOSED_TRADES_FILE}: {e}")

# ==============================================================================
# 3. 💾 FUNCIÓN DE ACTUALIZACIÓN DIARIA DE PIVOTES Y RESUMEN
# ==============================================================================

def actualizar_pivotes_diarios():
    """Calcula Pivotes, los guarda y envía resumen de operaciones del día anterior."""
    try:
        with open(SYMBOLS_FILE, 'r') as f:
            symbols = json.load(f)
    except FileNotFoundError:
        print(f"❌ Error: Archivo {SYMBOLS_FILE} no encontrado. Ejecuta el escaneo inicial.")
        return False

    closed_trades_list = load_closed_trades()
    if closed_trades_list:
        ganadoras = sum(1 for t in closed_trades_list if t['status'] == 'CLOSED_TP')
        perdedoras = sum(1 for t in closed_trades_list if t['status'] == 'CLOSED_SL')
        total = len(closed_trades_list)
        
        mensaje_resumen = f"📊 **RESUMEN DEL DÍA ANTERIOR** 📊\n"
        mensaje_resumen += f"Ganadoras (TP): {ganadoras}\n"
        mensaje_resumen += f"Perdedoras (SL): {perdedoras}\n"
        mensaje_resumen += f"Total de Operaciones Cerradas: {total}"
        enviar_telegram(mensaje_resumen)
        
        save_closed_trades([]) 

    all_pivots = {}
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"\n--- ⏳ INICIANDO CÁLCULO DIARIO DE PIVOTES ({today_utc}) ---")
    
    for i, symbol in enumerate(symbols):
        try:
            klines_daily = client.futures_historical_klines(symbol, Client.KLINE_INTERVAL_1DAY, "2 day ago", limit=2)
            if len(klines_daily) < 2: continue

            high_d, low_d, close_d = [float(klines_daily[-2][i]) for i in [2, 3, 4]]
            pivotes = calcular_pivotes_fibonacci(high_d, low_d, close_d)
            
            all_pivots[symbol] = {
                'date': today_utc,
                'levels': pivotes
            }
            if i % 20 == 0: print(f"   Calculando... {symbol}")
                
        except Exception as e:
            print(f"   ❌ Error al calcular Pivotes para {symbol}: {e}")
            
    with open(PIVOTS_FILE, 'w') as f:
        json.dump(all_pivots, f, indent=4)
        
    print(f"--- ✅ {len(all_pivots)} Pivotes guardados en {PIVOTS_FILE} ---")
    enviar_telegram(f"⭐️ **PIVOTES ACTUALIZADOS** ⭐️\nSe calcularon los nuevos Pivotes para {len(all_pivots)} pares para el día {today_utc}.")
    return True

def verificar_y_actualizar_pivotes():
    """Verifica si los Pivotes ya fueron calculados para el día de hoy."""
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        with open(PIVOTS_FILE, 'r') as f:
            daily_data = json.load(f)
        if daily_data and any(data.get('date') == today_utc for data in daily_data.values()):
            return True 
        else:
            return actualizar_pivotes_diarios()
    except (FileNotFoundError, json.JSONDecodeError):
        return actualizar_pivotes_diarios()

# ==============================================================================
# 4. 📈 LÓGICA DE SEGUIMIENTO DE OPERACIONES (TP/SL)
# ==============================================================================

def check_active_trades(all_pivots):
    """Verifica el progreso de las operaciones activas (TP1, TP2, SL)."""
    active_trades = load_active_trades()
    if not active_trades: return
        
    updated_trades = active_trades.copy()
    closed_trades_list = load_closed_trades()

    for symbol, trade in active_trades.items():
        if trade.get('status') != 'OPEN': continue

        try:
            klines_15m = client.futures_historical_klines(symbol, Client.KLINE_INTERVAL_15MINUTE, "15m ago", limit=1)
            if not klines_15m: continue
            price = float(klines_15m[-1][4]) 
            
            pivotes = all_pivots.get(symbol, {}).get('levels', {})
            if not pivotes: continue

            tp1_level, tp2_level, sl_level = pivotes[trade['tp1_key']], pivotes[trade['tp2_key']], pivotes[trade['sl_key']]
            is_long = trade['entry_type'] == 'LONG'
            
            if (is_long and price < sl_level) or (not is_long and price > sl_level):
                mensaje = f"🛑 *OPERACIÓN FALLIDA {trade['entry_type']} EN {symbol}* 🛑\n"
                mensaje += f"Precio: {price:.4f} | Tocó SL ({trade['sl_key']}): {sl_level:.4f}"
                enviar_telegram(mensaje)
                
                trade.update({
                    'status': 'CLOSED_SL',
                    'close_price': price,
                    'close_date': datetime.now().isoformat()
                })
                closed_trades_list.append(trade)
                del updated_trades[symbol]
                continue
                
            if (is_long and price > tp2_level) or (not is_long and price < tp2_level):
                if not trade.get('tp1_hit'):
                    updated_trades[symbol]['tp1_hit'] = True

                mensaje = f"🎯 *TP2 LOGRADO* - {symbol} ({trade['entry_type']}) 🎯\n"
                mensaje += f"Precio: {price:.4f} | Nivel R2/S2: {tp2_level:.4f}"
                enviar_telegram(mensaje)
                
                trade.update({
                    'status': 'CLOSED_TP',
                    'tp2_hit': True,
                    'close_price': price,
                    'close_date': datetime.now().isoformat()
                })
                closed_trades_list.append(trade)
                del updated_trades[symbol]
                continue

            if (is_long and price > tp1_level) or (not is_long and price < tp1_level):
                if not trade.get('tp1_hit'):
                    mensaje = f"✅ *TP1 LOGRADO* - {symbol} ({trade['entry_type']}) ✅\n"
                    mensaje += f"Precio: {price:.4f} | Nivel R1/S1: {tp1_level:.4f}"
                    enviar_telegram(mensaje)
                    updated_trades[symbol]['tp1_hit'] = True
                    
        except Exception as e:
            print(f"❌ Error al chequear trade activo para {symbol}: {e}")

    save_active_trades(updated_trades)
    save_closed_trades(closed_trades_list)

# ==============================================================================
# 5. 🚦 DETECCIÓN DE NUEVAS SEÑALES CON ANÁLISIS DE VOLUMEN/EMAs
# ==============================================================================

def detect_new_signals(all_pivots):
    """Busca nuevos cruces EMA solo si no hay un trade activo."""
    active_trades = load_active_trades()
    
    for symbol, pivot_data in all_pivots.items():
        try:
            if symbol in active_trades: continue 
            pivotes = pivot_data['levels']
            R1, R2, S1, PP = pivotes['R1'], pivotes['R2'], pivotes['S1'], pivotes['PP']

            klines_15m = client.futures_historical_klines(symbol, Client.KLINE_INTERVAL_15MINUTE, "55 hour ago", limit=250)
            if len(klines_15m) < 201: continue

            df = pd.DataFrame(klines_15m, columns=['open_time', 'Open', 'High', 'Low', 'Close', 'Volume', 'close_time', 'Quote asset volume', 'Number of trades', 'Taker buy base asset volume', 'Taker buy quote asset volume', 'Ignore'])
            df[['Close', 'Volume']] = df[['Close', 'Volume']].astype(float)

            # --- CÁLCULO DE INDICADORES ---
            df['EMA24'] = df['Close'].ewm(span=24, adjust=False).mean()
            df['EMA50'] = df['Close'].ewm(span=50, adjust=False).mean()
            df['EMA100'] = df['Close'].ewm(span=100, adjust=False).mean()
            df['EMA200'] = df['Close'].ewm(span=200, adjust=False).mean()

            # RSI (14)
            delta = df['Close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            df['RSI'] = 100 - (100 / (1 + rs))

            # MACD (12, 26, 9)
            df['EMA12'] = df['Close'].ewm(span=12, adjust=False).mean()
            df['EMA26'] = df['Close'].ewm(span=26, adjust=False).mean()
            df['MACD_line'] = df['EMA12'] - df['EMA26']
            df['MACD_signal'] = df['MACD_line'].ewm(span=9, adjust=False).mean()
            df['MACD_hist'] = df['MACD_line'] - df['MACD_signal']

            # Bandas de Bollinger (20, 2)
            df['BB_middle'] = df['Close'].rolling(window=20).mean()
            std_dev = df['Close'].rolling(window=20).std()
            df['BB_upper'] = df['BB_middle'] + (std_dev * 2)
            df['BB_lower'] = df['BB_middle'] - (std_dev * 2)
            
            # --- ANÁLISIS DE VOLUMEN (AMBOS MÉTODOS) ---
            df['Volume_MA20'] = df['Volume'].rolling(window=20).mean()

            # --- DATOS DE LA ÚLTIMA VELA CERRADA ---
            last = df.iloc[-1]
            prev = df.iloc[-2]
            
            price_last_closed = last['Close']
            
            cruce_alcista = (prev['EMA24'] < prev['EMA50']) and (last['EMA24'] > last['EMA50'])
            cruce_bajista = (prev['EMA24'] > prev['EMA50']) and (last['EMA24'] < last['EMA50'])
            
            # Método 1: Porcentaje vs. hora anterior
            vol_hora_ant = df['Volume'].iloc[-5:-1].mean()
            vol_pct_change = ((last['Volume'] - vol_hora_ant) / vol_hora_ant) * 100 if vol_hora_ant > 0 else 0

            # Método 2: Ratio vs. media móvil de 20 períodos
            vol_ratio = last['Volume'] / last['Volume_MA20'] if last['Volume_MA20'] > 0 else 0
            
            ema_context = {
                'sobre_ema100': price_last_closed > last['EMA100'],
                'sobre_ema200': price_last_closed > last['EMA200']
            }
            
            # --- LÓGICA DE ENTRADA (SIN CAMBIOS) ---
            new_trade_data = {
                'status': 'OPEN',
                'entry_price': price_last_closed,
                'tp1_hit': False, 'tp2_hit': False,
                'entry_date': datetime.now().isoformat(),
                # Datos originales guardados
                'vol_pct_change_entry': round(vol_pct_change, 2),
                'ema_100_context': ema_context['sobre_ema100'],
                'ema_200_context': ema_context['sobre_ema200'],
                # Nuevos datos para análisis
                'vol_ratio_entry': round(vol_ratio, 2),
                'rsi_entry': round(last['RSI'], 2) if pd.notna(last['RSI']) else None,
                'macd_hist_entry': round(last['MACD_hist'], 6) if pd.notna(last['MACD_hist']) else None,
                'bb_upper_entry': round(last['BB_upper'], 4) if pd.notna(last['BB_upper']) else None,
                'bb_lower_entry': round(last['BB_lower'], 4) if pd.notna(last['BB_lower']) else None,
            }

            if cruce_alcista and (S1 < price_last_closed < R1):
                new_trade_data.update({
                    'entry_type': 'LONG',
                    'tp1_key': 'R1', 'tp2_key': 'R2', 'sl_key': 'S1'
                })
                active_trades[symbol] = new_trade_data
                save_active_trades(active_trades)

                mensaje = f"🚀 *NUEVA COMPRA EN {symbol} (15M)*\n"
                mensaje += f"Precio Entrada: {price_last_closed:.4f} | PP: {PP:.4f}\n"
                mensaje += f"Contexto EMA: 100({'✅' if ema_context['sobre_ema100'] else '❌'}) 200({'✅' if ema_context['sobre_ema200'] else '❌'})"
                enviar_telegram(mensaje)
                print(f"✅ NUEVA COMPRA detectada: {symbol}")
                
            elif cruce_bajista and (PP < price_last_closed < R2):
                new_trade_data.update({
                    'entry_type': 'SHORT',
                    'tp1_key': 'PP', 'tp2_key': 'S1', 'sl_key': 'R2'
                })
                active_trades[symbol] = new_trade_data
                save_active_trades(active_trades)

                mensaje = f"🔻 *NUEVA VENTA EN {symbol} (15M)*\n"
                mensaje += f"Precio Entrada: {price_last_closed:.4f} | PP: {PP:.4f}\n"
                mensaje += f"Contexto EMA: 100({'✅' if not ema_context['sobre_ema100'] else '❌'}) 200({'✅' if not ema_context['sobre_ema200'] else '❌'})"
                enviar_telegram(mensaje)
                print(f"✅ NUEVA VENTA detectada: {symbol}")

        except Exception as e:
            print(f"❌ Error al procesar {symbol} en detección de señales: {e}")

# ==============================================================================
# 6. 🔄 BUCLE PRINCIPAL
# ==============================================================================

def iniciar_monitoreo():
    """Bucle principal que ejecuta el escaneo cada 15 minutos."""
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
            print("⚠️ Advertencia: El ciclo de escaneo tardó más de 15 minutos.")
            
if __name__ == '__main__':
    iniciar_monitoreo()
