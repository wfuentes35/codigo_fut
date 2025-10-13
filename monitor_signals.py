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
TRADES_FILE = 'active_trades.json' # Nuevo archivo para seguimiento de operaciones

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
    """Guarda el estado de las operaciones activas."""
    try:
        with open(TRADES_FILE, 'w') as f:
            json.dump(trades, f, indent=4)
    except Exception as e:
        print(f"❌ Error al guardar {TRADES_FILE}: {e}")

# ==============================================================================
# 3. 💾 FUNCIÓN DE ACTUALIZACIÓN DIARIA DE PIVOTES
# ==============================================================================

# (Se mantiene la función actualizar_pivotes_diarios y verificar_y_actualizar_pivotes
#  exactamente igual a la última versión para asegurar la estabilidad del proceso diario)

def actualizar_pivotes_diarios():
    """Calcula los Pivotes del día anterior para todos los 100 pares y los guarda."""
    try:
        with open(SYMBOLS_FILE, 'r') as f:
            symbols = json.load(f)
    except FileNotFoundError:
        print(f"❌ Error: Archivo {SYMBOLS_FILE} no encontrado. Ejecuta el escaneo inicial.")
        return False

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
            if i % 20 == 0:
                print(f"   Calculando... {symbol}")
                
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
        
        # Verificar la fecha de cualquier par
        if daily_data and any(data.get('date') == today_utc for data in daily_data.values()):
            return True # Ya están actualizados
        else:
            return actualizar_pivotes_diarios() # No están actualizados, calcular ahora

    except (FileNotFoundError, json.JSONDecodeError):
        # Si el archivo no existe o está corrupto, actualizar
        return actualizar_pivotes_diarios()

# ==============================================================================
# 4. 📈 LÓGICA DE SEGUIMIENTO DE OPERACIONES (TP/SL)
# ==============================================================================

def check_active_trades(all_pivots):
    """Verifica el progreso de las operaciones activas (TP1, TP2, SL)."""
    
    active_trades = load_active_trades()
    updated_trades = active_trades.copy()

    for symbol, trade in active_trades.items():
        if trade.get('status') != 'OPEN':
            continue

        try:
            # 1. Obtener precio actual (last close price)
            klines_15m = client.futures_historical_klines(symbol, Client.KLINE_INTERVAL_15MINUTE, "15m ago", limit=1)
            if not klines_15m: continue
            price = float(klines_15m[-1][4]) # Close price of the last closed candle
            
            # 2. Obtener Pivotes del JSON
            pivotes = all_pivots.get(symbol, {}).get('levels', {})
            if not pivotes: continue

            tp1_level = pivotes[trade['tp1_key']]
            tp2_level = pivotes[trade['tp2_key']]
            sl_level = pivotes[trade['sl_key']]
            
            is_long = trade['entry_type'] == 'LONG'
            
            # --- COMPROBACIÓN DE TP y SL ---
            
            # 3. Comprobar SL (Operación Fallida)
            # Long: Precio < SL | Short: Precio > SL
            if (is_long and price < sl_level) or (not is_long and price > sl_level):
                mensaje = f"🛑 *OPERACIÓN FALLIDA {trade['entry_type']} EN {symbol}* 🛑\n"
                mensaje += f"Precio: {price:.4f} | Tocó SL ({trade['sl_key']}): {sl_level:.4f}\n"
                print(f"🛑 SL: {symbol} - {trade['entry_type']}")
                enviar_telegram(mensaje)
                updated_trades[symbol]['status'] = 'CLOSED_SL'
                continue # Pasa al siguiente trade
                
            # 4. Comprobar TP2
            # Long: Precio > TP2 | Short: Precio < TP2
            if (is_long and price > tp2_level) or (not is_long and price < tp2_level):
                if not trade.get('tp2_hit'):
                    mensaje = f"🎯 *TP2 LOGRADO* - {symbol} ({trade['entry_type']}) 🎯\n"
                    mensaje += f"Precio: {price:.4f} | Nivel R2/S2: {tp2_level:.4f}\n"
                    print(f"🎯 TP2: {symbol} - {trade['entry_type']}")
                    enviar_telegram(mensaje)
                    updated_trades[symbol]['tp2_hit'] = True
                    updated_trades[symbol]['status'] = 'CLOSED_TP' # Cerramos la operación
                continue

            # 5. Comprobar TP1
            # Long: Precio > TP1 | Short: Precio < TP1
            if (is_long and price > tp1_level) or (not is_long and price < tp1_level):
                if not trade.get('tp1_hit'):
                    mensaje = f"✅ *TP1 LOGRADO* - {symbol} ({trade['entry_type']}) ✅\n"
                    mensaje += f"Precio: {price:.4f} | Nivel R1/S1: {tp1_level:.4f}\n"
                    print(f"✅ TP1: {symbol} - {trade['entry_type']}")
                    enviar_telegram(mensaje)
                    updated_trades[symbol]['tp1_hit'] = True
                    
        except Exception as e:
            print(f"❌ Error al chequear trade activo para {symbol}: {e}")

    # Guardar solo las operaciones que aún están activas (OPEN)
    final_trades = {s: t for s, t in updated_trades.items() if t['status'] == 'OPEN'}
    save_active_trades(final_trades)
    
# ==============================================================================
# 5. 🚦 DETECCIÓN DE NUEVAS SEÑALES
# ==============================================================================

def detect_new_signals(all_pivots):
    """Busca nuevos cruces EMA solo si no hay un trade activo."""

    # Cargamos trades activos para no sobre-abrir
    active_trades = load_active_trades()
    
    for symbol, pivot_data in all_pivots.items():
        try:
            # Si ya hay un trade abierto, saltar detección de nueva señal
            if symbol in active_trades and active_trades[symbol].get('status') == 'OPEN':
                continue
                
            # Desempaquetar Pivotes
            pivotes = pivot_data['levels']
            R1, R2, S1, PP, S2 = pivotes['R1'], pivotes['R2'], pivotes['S1'], pivotes['PP'], pivotes['S2']

            # Obtener datos de 15m y calcular EMAs
            klines_15m = client.futures_historical_klines(symbol, Client.KLINE_INTERVAL_15MINUTE, "15 hour ago", limit=65)
            if len(klines_15m) < 51: continue

            df = pd.DataFrame(klines_15m, columns=['open_time', 'Open', 'High', 'Low', 'Close', 'Volume', 'close_time', 'Quote asset volume', 'Number of trades', 'Taker buy base asset volume', 'Taker buy quote asset volume', 'Ignore'])
            df['Close'] = df['Close'].astype(float)

            df['EMA24'] = df['Close'].ewm(span=24, adjust=False).mean()
            df['EMA50'] = df['Close'].ewm(span=50, adjust=False).mean()

            ema24_ant = df['EMA24'].iloc[-2]
            ema50_ant = df['EMA50'].iloc[-2]
            ema24_last_closed = df['EMA24'].iloc[-1]
            ema50_last_closed = df['EMA50'].iloc[-1]
            price_last_closed = df['Close'].iloc[-1]
            
            cruce_alcista = (ema24_ant < ema50_ant) and (ema24_last_closed > ema50_last_closed)
            cruce_bajista = (ema24_ant > ema50_ant) and (ema24_last_closed < ema50_last_closed)
            
            
            # --- LONG (COMPRA): S1 < Precio < R1 ---
            if cruce_alcista and (price_last_closed > S1 and price_last_closed < R1):
                
                # --- ABRIR NUEVO TRADE LONG ---
                active_trades[symbol] = {
                    'status': 'OPEN',
                    'entry_type': 'LONG',
                    'entry_price': price_last_closed,
                    'tp1_key': 'R1',
                    'tp2_key': 'R2',
                    'sl_key': 'S1', # Stop Loss en el nivel de soporte de la zona de entrada
                    'tp1_hit': False,
                    'tp2_hit': False
                }
                save_active_trades(active_trades)

                zona = f"S1 ({S1:.4f}) / R1 ({R1:.4f})"
                mensaje = f"🚀 *NUEVA COMPRA EN {symbol} (15M)*\n"
                mensaje += f"Cruce EMA24/50 ALCISTA detectado.\n"
                mensaje += f"Precio de Entrada: {price_last_closed:.4f} | PP: {PP:.4f}\n"
                mensaje += f"TP1 (R1): {R1:.4f} | SL (S1): {S1:.4f}\n"
                enviar_telegram(mensaje)
                print(f"✅ NUEVA COMPRA detectada: {symbol}")
                
            # --- SHORT (VENTA): P < Precio < R2 ---
            elif cruce_bajista and (price_last_closed > PP and price_last_closed < R2):
                
                # --- ABRIR NUEVO TRADE SHORT ---
                active_trades[symbol] = {
                    'status': 'OPEN',
                    'entry_type': 'SHORT',
                    'entry_price': price_last_closed,
                    'tp1_key': 'PP',
                    'tp2_key': 'S1',
                    'sl_key': 'R2', # Stop Loss en el nivel de resistencia de la zona de entrada
                    'tp1_hit': False,
                    'tp2_hit': False
                }
                save_active_trades(active_trades)

                zona = f"PP ({PP:.4f}) / R2 ({R2:.4f})"
                mensaje = f"🔻 *NUEVA VENTA EN {symbol} (15M)*\n"
                mensaje += f"Cruce EMA24/50 BAJISTA detectado.\n"
                mensaje += f"Precio de Entrada: {price_last_closed:.4f} | PP: {PP:.4f}\n"
                mensaje += f"TP1 (PP): {PP:.4f} | SL (R2): {R2:.4f}\n"
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
        
        # 1. Verificar y actualizar Pivotes (solo si es un nuevo día UTC)
        if not verificar_y_actualizar_pivotes():
            print("🛑 Fallo al actualizar Pivotes. Reintentando en el próximo ciclo.")
            
        # 2. Cargar los Pivotes del día para usar en las dos etapas
        try:
            with open(PIVOTS_FILE, 'r') as f:
                all_pivots = json.load(f)
        except Exception:
             all_pivots = {} # Si falla, usa un diccionario vacío

        # 3. Verificar si los trades activos ya tocaron TP o SL (gestión de riesgo)
        check_active_trades(all_pivots)
        
        # 4. Detectar si hay nuevas señales (apertura de trade)
        detect_new_signals(all_pivots)

        # 5. Control de tiempo
        tiempo_fin = time.time()
        duracion = tiempo_fin - tiempo_inicio
        tiempo_espera = INTERVALO_MONITOREO_SEG - duracion
        
        print(f"Ciclo completado en {duracion:.2f} segundos.")
        
        if tiempo_espera > 0:
            print(f"💤 Esperando {int(tiempo_espera)} segundos hasta el próximo ciclo...")
            time.sleep(tiempo_espera)
        else:
            print("⚠️ Advertencia: El ciclo de escaneo tardó más de 15 minutos.")
            
if __name__ == '__main__':
    iniciar_monitoreo()
