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
# Credenciales obtenidas del archivo .env
API_KEY = os.getenv("API_KEY")
SECRET_KEY = os.getenv("SECRET_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Inicialización de la API de Binance
if not API_KEY or not SECRET_KEY:
    raise ValueError("ERROR: Las claves API_KEY o SECRET_KEY no se encontraron en el archivo .env.")
client = Client(API_KEY, SECRET_KEY)

# Variables globales para el estado de los Pivotes
PIVOT_DATA = None
LAST_PIVOT_DATE = None
PIVOT_SYMBOL = "BTCUSDT" # Símbolo de referencia para el cálculo de Pivotes
INTERVALO_MONITOREO_SEG = 900 # 15 minutos

# ==============================================================================
# 2. 🧮 FÓRMULAS Y UTILIDADES
# ==============================================================================

def calcular_pivotes_fibonacci(high, low, close):
    """Calcula los niveles de Pivote Fibonacci usando PP Clásico (fórmula estándar)."""
    rango = high - low
    PP = (high + low + close) / 3 
    
    # Multiplicadores de Fibonacci
    FIB_382, FIB_618, FIB_100 = 0.382, 0.618, 1.000
    
    R1 = PP + (rango * FIB_382)
    R2 = PP + (rango * FIB_618)
    R3 = PP + (rango * FIB_100) 
    S1 = PP - (rango * FIB_382)
    S2 = PP - (rango * FIB_618)
    S3 = PP - (rango * FIB_100)
    
    return {'PP': PP, 'R1': R1, 'R2': R2, 'R3': R3, 'S1': S1, 'S2': S2, 'S3': S3}

def enviar_telegram(mensaje):
    """Envía un mensaje a través de la API de Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Error: Configuración de Telegram incompleta. No se puede enviar el mensaje.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': mensaje,
        'parse_mode': 'Markdown'
    }
    try:
        requests.post(url, data=payload)
    except Exception as e:
        print(f"❌ Error al enviar mensaje a Telegram: {e}")

def get_pivots_if_needed():
    """Calcula o actualiza los Pivotes solo si la fecha UTC ha cambiado."""
    global PIVOT_DATA, LAST_PIVOT_DATE
    
    # La fecha UTC determina el nuevo día de trading de Binance
    today_utc = datetime.now(timezone.utc).date()

    # Si es la primera vez (None) o si la fecha ha cambiado, recalcular
    if PIVOT_DATA is None or LAST_PIVOT_DATE != today_utc:
        try:
            # Obtenemos la vela cerrada del día anterior
            klines_daily = client.futures_historical_klines(PIVOT_SYMBOL, Client.KLINE_INTERVAL_1DAY, "2 day ago", limit=2)
            
            if len(klines_daily) < 2:
                print("❌ No hay datos diarios para calcular Pivotes.")
                return False

            # Usamos la vela cerrada (índice -2)
            high_d, low_d, close_d = [float(klines_daily[-2][i]) for i in [2, 3, 4]]
            
            # Recalcular y actualizar globales
            PIVOT_DATA = calcular_pivotes_fibonacci(high_d, low_d, close_d)
            LAST_PIVOT_DATE = today_utc
            
            print(f"✅ PIVOTES ACTUALIZADOS para el día: {today_utc}. PP: {PIVOT_DATA['PP']:.2f}")
            enviar_telegram(f"⭐️ **ACTUALIZACIÓN DIARIA DE PIVOTES** ⭐️\nPP del día: {PIVOT_DATA['PP']:.2f}\nR1: {PIVOT_DATA['R1']:.2f} | S1: {PIVOT_DATA['S1']:.2f}")
            return True
        
        except Exception as e:
            print(f"❌ Error al calcular/actualizar Pivotes: {e}")
            return False
            
    return True # Los Pivotes ya están actualizados para hoy

# ==============================================================================
# 3. 🚦 LÓGICA DE MONITOREO Y SEÑALES
# ==============================================================================

def buscar_cruces_y_alertar():
    """Busca cruces EMA en las zonas de Pivotes y envía alertas."""
    global PIVOT_DATA
    
    if not get_pivots_if_needed():
        print("🛑 No se pudieron obtener/actualizar los Pivotes. Saltando el ciclo de monitoreo.")
        return

    try:
        with open('top_100_symbols.json', 'r') as f:
            symbols = json.load(f)
    except FileNotFoundError:
        print("❌ Archivo 'top_100_symbols.json' no encontrado. Ejecuta el escaneo inicial.")
        return
        
    # Desempaquetar los Pivotes
    R1, R2, S1, PP = PIVOT_DATA['R1'], PIVOT_DATA['R2'], PIVOT_DATA['S1'], PIVOT_DATA['PP']

    # Monitoreo
    for symbol in symbols:
        try:
            # Obtener datos de 15m (al menos 51 velas para EMA 50)
            klines_15m = client.futures_historical_klines(symbol, Client.KLINE_INTERVAL_15MINUTE, "15 hour ago", limit=65)
            if len(klines_15m) < 51: continue

            df = pd.DataFrame(klines_15m, columns=['open_time', 'Open', 'High', 'Low', 'Close', 'Volume', 'close_time', 'Quote asset volume', 'Number of trades', 'Taker buy base asset volume', 'Taker buy quote asset volume', 'Ignore'])
            df['Close'] = df['Close'].astype(float)

            # Calcular EMAs
            df['EMA24'] = df['Close'].ewm(span=24, adjust=False).mean()
            df['EMA50'] = df['Close'].ewm(span=50, adjust=False).mean()

            # Datos de la última vela CERRADA
            ema24_ant = df['EMA24'].iloc[-2]
            ema50_ant = df['EMA50'].iloc[-2]
            ema24_last_closed = df['EMA24'].iloc[-1]
            ema50_last_closed = df['EMA50'].iloc[-1]
            price_last_closed = df['Close'].iloc[-1]
            
            # Condición de cruce: el cruce debe ocurrir en la última vela cerrada
            cruce_alcista = (ema24_ant < ema50_ant) and (ema24_last_closed > ema50_last_closed)
            cruce_bajista = (ema24_ant > ema50_ant) and (ema24_last_closed < ema50_last_closed)
            
            
            # --- ZONA DE COMPRA: S1 < Precio < R1 ---
            if cruce_alcista and (price_last_closed > S1 and price_last_closed < R1):
                zona = f"S1 ({S1:.2f}) / R1 ({R1:.2f})"
                mensaje = f"🚀 *SEÑAL DE COMPRA EN {symbol} (15M)*\n"
                mensaje += f"Cruce EMA24/50 ALCISTA detectado.\n"
                mensaje += f"Precio: {price_last_closed:.2f}\n"
                mensaje += f"Zona: {zona}"
                enviar_telegram(mensaje)
                print(f"✅ COMPRA detectada: {symbol} en {zona}")
                
            # --- ZONA DE VENTA: P < Precio < R2 ---
            elif cruce_bajista and (price_last_closed > PP and price_last_closed < R2):
                zona = f"PP ({PP:.2f}) / R2 ({R2:.2f})"
                mensaje = f"🔻 *SEÑAL DE VENTA EN {symbol} (15M)*\n"
                mensaje += f"Cruce EMA24/50 BAJISTA detectado.\n"
                mensaje += f"Precio: {price_last_closed:.2f}\n"
                mensaje += f"Zona: {zona}"
                enviar_telegram(mensaje)
                print(f"✅ VENTA detectada: {symbol} en {zona}")

        except Exception as e:
            # Ignoramos pares que fallen la descarga de datos por liquidez o error temporal.
            print(f"❌ Error al procesar {symbol}: {e}")

# ==============================================================================
# 4. 🔄 BUCLE PRINCIPAL (TMUX)
# ==============================================================================

def iniciar_monitoreo():
    """Bucle principal que ejecuta el escaneo cada 15 minutos."""
    print("--- 🤖 Iniciando monitoreo de señales (15m) ---")
    while True:
        tiempo_inicio = time.time()
        
        # 1. Ejecutar la búsqueda de señales
        buscar_cruces_y_alertar()
        
        tiempo_fin = time.time()
        duracion = tiempo_fin - tiempo_inicio
        
        # 2. Esperar exactamente hasta que se cumplan los 15 minutos
        tiempo_espera = INTERVALO_MONITOREO_SEG - duracion
        
        print(f"Ciclo completado en {duracion:.2f} segundos.")
        
        if tiempo_espera > 0:
            print(f"💤 Esperando {int(tiempo_espera)} segundos hasta el próximo ciclo...")
            time.sleep(tiempo_espera)
        else:
            print("⚠️ Advertencia: El ciclo de escaneo tardó más de 15 minutos.")
            
if __name__ == '__main__':
    iniciar_monitoreo()