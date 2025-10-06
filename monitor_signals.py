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

# Nombres de archivos
SYMBOLS_FILE = 'top_100_symbols.json'
PIVOTS_FILE = 'daily_pivots.json'

INTERVALO_MONITOREO_SEG = 900 # 15 minutos

# ==============================================================================
# 2. 🧮 FÓRMULAS Y UTILIDADES
# ==============================================================================

def calcular_pivotes_fibonacci(high, low, close):
    """Calcula los niveles de Pivote Fibonacci usando PP Clásico (fórmula estándar)."""
    rango = high - low
    PP = (high + low + close) / 3 
    
    FIB_382, FIB_618, FIB_100 = 0.382, 0.618, 1.000
    
    R1 = PP + (rango * FIB_382)
    R2 = PP + (rango * FIB_618)
    R3 = PP + (rango * FIB_100) 
    S1 = PP - (rango * FIB_382)
    S2 = PP - (rango * FIB_618)
    S3 = PP - (rango * FIB_100)
    
    # Redondeamos a 4 decimales para consistencia
    return {k: round(v, 4) for k, v in {'PP': PP, 'R1': R1, 'R2': R2, 'R3': R3, 'S1': S1, 'S2': S2, 'S3': S3}.items()}

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

# ==============================================================================
# 3. 💾 FUNCIÓN DE ACTUALIZACIÓN DIARIA (SOLO UNA VEZ AL DÍA)
# ==============================================================================

def actualizar_pivotes_diarios():
    """
    Calcula los Pivotes del día anterior para todos los 100 pares y los guarda 
    en un JSON. Solo se ejecuta si la fecha guardada es de AYER.
    """
    try:
        with open(SYMBOLS_FILE, 'r') as f:
            symbols = json.load(f)
    except FileNotFoundError:
        print(f"❌ Error: Archivo {SYMBOLS_FILE} no encontrado. Ejecuta el escaneo inicial.")
        return False

    all_pivots = {}
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    print(f"\n--- ⏳ INICIANDO CÁLCULO DIARIO DE PIVOTES ({today_utc}) ---")
    
    # Este proceso puede tardar un poco (100+ llamadas API)
    for i, symbol in enumerate(symbols):
        try:
            # Obtener datos de la vela cerrada del día anterior
            klines_daily = client.futures_historical_klines(symbol, Client.KLINE_INTERVAL_1DAY, "2 day ago", limit=2)
            
            if len(klines_daily) < 2:
                print(f"   ⚠️ Datos diarios insuficientes para {symbol}. Omitiendo.")
                continue

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
            
    # Guardar todos los Pivotes calculados
    with open(PIVOTS_FILE, 'w') as f:
        json.dump(all_pivots, f)
        
    print(f"--- ✅ {len(all_pivots)} Pivotes guardados en {PIVOTS_FILE} ---")
    enviar_telegram(f"⭐️ **PIVOTES ACTUALIZADOS** ⭐️\nSe calcularon los nuevos Pivotes para {len(all_pivots)} pares para el día {today_utc}.")
    return True

def verificar_y_actualizar_pivotes():
    """Verifica si los Pivotes ya fueron calculados para el día de hoy."""
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        with open(PIVOTS_FILE, 'r') as f:
            daily_data = json.load(f)
        
        # Tomar cualquier par para verificar la fecha
        if daily_data and any(data.get('date') == today_utc for data in daily_data.values()):
            return True # Ya están actualizados
        else:
            return actualizar_pivotes_diarios() # No están actualizados, calcular ahora

    except (FileNotFoundError, json.JSONDecodeError):
        # Si el archivo no existe o está corrupto, actualizar
        return actualizar_pivotes_diarios()

# ==============================================================================
# 4. 🚦 LÓGICA DE MONITOREO (USANDO DATOS DEL JSON)
# ==============================================================================

def buscar_cruces_y_alertar():
    """Busca cruces EMA en las zonas de Pivotes, LEYENDO el JSON."""
    
    # 1. VERIFICAR SI HAY QUE CALCULAR LOS PIVOTES
    if not verificar_y_actualizar_pivotes():
        print("🛑 Error: No se pudo obtener la data de Pivotes para hoy. Intentando en el próximo ciclo.")
        return

    # 2. CARGAR TODOS LOS PIVOTES DEL JSON
    try:
        with open(PIVOTS_FILE, 'r') as f:
            all_pivots = json.load(f)
    except Exception as e:
        print(f"❌ Error al leer {PIVOTS_FILE}: {e}")
        return
        
    # 3. MONITOREO CADA 15 MINUTOS
    for symbol, pivot_data in all_pivots.items():
        try:
            # Desempaquetar los Pivotes
            pivotes = pivot_data['levels']
            R1, R2, S1, PP = pivotes['R1'], pivotes['R2'], pivotes['S1'], pivotes['PP']

            # Obtener datos de 15m y calcular EMAs
            klines_15m = client.futures_historical_klines(symbol, Client.KLINE_INTERVAL_15MINUTE, "15 hour ago", limit=65)
            if len(klines_15m) < 51: continue

            df = pd.DataFrame(klines_15m, columns=['open_time', 'Open', 'High', 'Low', 'Close', 'Volume', 'close_time', 'Quote asset volume', 'Number of trades', 'Taker buy base asset volume', 'Taker buy quote asset volume', 'Ignore'])
            df['Close'] = df['Close'].astype(float)

            df['EMA24'] = df['Close'].ewm(span=24, adjust=False).mean()
            df['EMA50'] = df['Close'].ewm(span=50, adjust=False).mean()

            # Datos de la última vela CERRADA
            ema24_ant = df['EMA24'].iloc[-2]
            ema50_ant = df['EMA50'].iloc[-2]
            ema24_last_closed = df['EMA24'].iloc[-1]
            ema50_last_closed = df['EMA50'].iloc[-1]
            price_last_closed = df['Close'].iloc[-1]
            
            cruce_alcista = (ema24_ant < ema50_ant) and (ema24_last_closed > ema50_last_closed)
            cruce_bajista = (ema24_ant > ema50_ant) and (ema24_last_closed < ema50_last_closed)
            
            
            # --- ZONA DE COMPRA: S1 < Precio < R1 ---
            if cruce_alcista and (price_last_closed > S1 and price_last_closed < R1):
                zona = f"S1 ({S1:.4f}) / R1 ({R1:.4f})"
                mensaje = f"🚀 *COMPRA {symbol} (15M)*\n"
                mensaje += f"Cruce EMA24/50 ALCISTA.\n"
                mensaje += f"Precio: {price_last_closed:.4f} | PP: {PP:.4f}\n"
                mensaje += f"Zona: {zona}"
                enviar_telegram(mensaje)
                print(f"✅ COMPRA detectada: {symbol}")
                
            # --- ZONA DE VENTA: P < Precio < R2 ---
            elif cruce_bajista and (price_last_closed > PP and price_last_closed < R2):
                zona = f"PP ({PP:.4f}) / R2 ({R2:.4f})"
                mensaje = f"🔻 *VENTA {symbol} (15M)*\n"
                mensaje += f"Cruce EMA24/50 BAJISTA.\n"
                mensaje += f"Precio: {price_last_closed:.4f} | PP: {PP:.4f}\n"
                mensaje += f"Zona: {zona}"
                enviar_telegram(mensaje)
                print(f"✅ VENTA detectada: {symbol}")

        except Exception as e:
            print(f"❌ Error al procesar {symbol}: {e}")

# ==============================================================================
# 5. 🔄 BUCLE PRINCIPAL (TMUX)
# ==============================================================================

def iniciar_monitoreo():
    """Bucle principal que ejecuta el escaneo cada 15 minutos."""
    print("--- 🤖 Iniciando monitoreo de señales (15m) ---")
    while True:
        tiempo_inicio = time.time()
        
        # Ejecutar la búsqueda de señales y el control de actualización de Pivotes
        buscar_cruces_y_alertar()
        
        tiempo_fin = time.time()
        duracion = tiempo_fin - tiempo_inicio
        
        # Esperar exactamente hasta que se cumplan los 15 minutos
        tiempo_espera = INTERVALO_MONITOREO_SEG - duracion
        
        print(f"Ciclo completado en {duracion:.2f} segundos.")
        
        if tiempo_espera > 0:
            print(f"💤 Esperando {int(tiempo_espera)} segundos hasta el próximo ciclo...")
            time.sleep(tiempo_espera)
        else:
            print("⚠️ Advertencia: El ciclo de escaneo tardó más de 15 minutos.")
            
if __name__ == '__main__':
    iniciar_monitoreo()
