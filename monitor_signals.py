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
CLOSED_TRADES_FILE = 'closed_trades.json' # Nuevo archivo para histórico

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
            json.dump(trades_list, f, indent=4)
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

    # 1. ENVIAR RESUMEN DE OPERACIONES CERRADAS DEL DÍA ANTERIOR
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
        
        # Opcional: Limpiar el archivo para comenzar el nuevo día limpio (sino se acumula)
        save_closed_trades([]) 

    # 2. CÁLCULO DE PIVOTES
    all_pivots = {}
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"\n--- ⏳ INICIANDO CÁLCULO DIARIO DE PIVOTES ({today_utc}) ---")
    
    for i, symbol in enumerate(symbols):
        # ... (Lógica de cálculo de pivotes igual que antes) ...
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
    # (Lógica igual que antes)
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
    updated_trades = active_trades.copy()
    closed_trades_list = load_closed_trades()

    for symbol, trade in active_trades.items():
        if trade.get('status') != 'OPEN':
            continue

        try:
            # 1. Obtener precio actual y Pivotes
            klines_15m = client.futures_historical_klines(symbol, Client.KLINE_INTERVAL_15MINUTE, "15m ago", limit=1)
            if not klines_15m: continue
            price = float(klines_15m[-1][4]) 
            
            pivotes = all_pivots.get(symbol, {}).get('levels', {})
            if not pivotes: continue

            tp1_level = pivotes[trade['tp1_key']]
            tp2_level = pivotes[trade['tp2_key']]
            sl_level = pivotes[trade['sl_key']]
            
            is_long = trade['entry_type'] == 'LONG'
            
            # --- COMPROBACIÓN DE TP y SL ---
            
            # 2. Comprobar SL (Operación Fallida)
            if (is_long and price < sl_level) or (not is_long and price > sl_level):
                # Obtener datos de EMA 100/200 para la documentación de falla
                data_15m = client.futures_historical_klines(symbol, Client.KLINE_INTERVAL_15MINUTE, "25 hour ago", limit=105)
                df = pd.DataFrame(data_15m, columns=['open_time', 'Open', 'High', 'Low', 'Close', 'Volume', 'close_time', 'Quote asset volume', 'Number of trades', 'Taker buy base asset volume', 'Taker buy quote asset volume', 'Ignore'])
                df['Close'] = df['Close'].astype(float)
                df['EMA100'] = df['Close'].ewm(span=100, adjust=False).mean()
                df['EMA200'] = df['Close'].ewm(span=200, adjust=False).mean()

                ema100_last = df['EMA100'].iloc[-1]
                ema200_last = df['EMA200'].iloc[-1]
                
                # Documentar la razón de la falla
                razon_falla = "Precio por debajo de PP"
                if is_long:
                    if price < ema100_last: razon_falla += " | Bajo EMA100"
                    if price < ema200_last: razon_falla += " | Bajo EMA200"
                else: # SHORT
                    if price > ema100_last: razon_falla += " | Sobre EMA100"
                    if price > ema200_last: razon_falla += " | Sobre EMA200"
                
                # Enviar alerta por Telegram
                mensaje = f"🛑 *OPERACIÓN FALLIDA {trade['entry_type']} EN {symbol}* 🛑\n"
                mensaje += f"Precio: {price:.4f} | Tocó SL ({trade['sl_key']}): {sl_level:.4f}\n"
                mensaje += f"Razón de Falla (Al Cierre): {razon_falla}"
                enviar_telegram(mensaje)
                
                # Mover a historial de cerrados
                trade['status'] = 'CLOSED_SL'
                trade['close_price'] = price
                trade['close_date'] = datetime.now().isoformat()
                trade['failure_reason'] = razon_falla
                closed_trades_list.append(trade)
                del updated_trades[symbol]
                continue
                
            # 3. Comprobar TP2 (Cierra operación con Ganancia Máxima)
            if (is_long and price > tp2_level) or (not is_long and price < tp2_level):
                if not trade.get('tp2_hit'):
                    mensaje = f"🎯 *TP2 LOGRADO* - {symbol} ({trade['entry_type']}) 🎯\n"
                    mensaje += f"Precio: {price:.4f} | Nivel R2/S2: {tp2_level:.4f}"
                    enviar_telegram(mensaje)
                    
                    # Mover a historial de cerrados
                    trade['status'] = 'CLOSED_TP'
                    trade['tp2_hit'] = True
                    trade['close_price'] = price
                    trade['close_date'] = datetime.now().isoformat()
                    closed_trades_list.append(trade)
                    del updated_trades[symbol]
                continue

            # 4. Comprobar TP1 (Marca Hit para monitorear TP2)
            if (is_long and price > tp1_level) or (not is_long and price < tp1_level):
                if not trade.get('tp1_hit'):
                    mensaje = f"✅ *TP1 LOGRADO* - {symbol} ({trade['entry_type']}) ✅\n"
                    mensaje += f"Precio: {price:.4f} | Nivel R1/S1: {tp1_level:.4f}"
                    enviar_telegram(mensaje)
                    updated_trades[symbol]['tp1_hit'] = True
                    
        except Exception as e:
            print(f"❌ Error al chequear trade activo para {symbol}: {e}")

    # 5. Guardar estado
    save_active_trades(updated_trades)
    save_closed_trades(closed_trades_list)

# ==============================================================================
# 5. 🚦 DETECCIÓN DE NUEVAS SEÑALES CON ANÁLISIS DE VOLUMEN/EMAs
# ==============================================================================

def detect_new_signals(all_pivots):
    """Busca nuevos cruces EMA solo si no hay un trade activo, analizando volumen y EMAs lentas."""

    active_trades = load_active_trades()
    
    for symbol, pivot_data in all_pivots.items():
        try:
            if symbol in active_trades and active_trades[symbol].get('status') == 'OPEN':
                continue
                
            # Desempaquetar Pivotes
            pivotes = pivot_data['levels']
            R1, R2, S1, PP = pivotes['R1'], pivotes['R2'], pivotes['S1'], pivotes['PP']

            # Obtener datos de 15m (necesitamos 200+100+50 velas para todas las EMAs y volumen)
            klines_15m = client.futures_historical_klines(symbol, Client.KLINE_INTERVAL_15MINUTE, "55 hour ago", limit=250)
            if len(klines_15m) < 201: continue

            df = pd.DataFrame(klines_15m, columns=['open_time', 'Open', 'High', 'Low', 'Close', 'Volume', 'close_time', 'Quote asset volume', 'Number of trades', 'Taker buy base asset volume', 'Taker buy quote asset volume', 'Ignore'])
            df['Close'] = df['Close'].astype(float)
            df['Volume'] = df['Volume'].astype(float)

            # Calcular EMAs
            df['EMA24'] = df['Close'].ewm(span=24, adjust=False).mean()
            df['EMA50'] = df['Close'].ewm(span=50, adjust=False).mean()
            df['EMA100'] = df['Close'].ewm(span=100, adjust=False).mean()
            df['EMA200'] = df['Close'].ewm(span=200, adjust=False).mean()

            # Datos de la última vela CERRADA
            ema24_ant = df['EMA24'].iloc[-2]
            ema50_ant = df['EMA50'].iloc[-2]
            price_last_closed = df['Close'].iloc[-1]
            ema100_last = df['EMA100'].iloc[-1]
            ema200_last = df['EMA200'].iloc[-1]

            cruce_alcista = (ema24_ant < ema50_ant) and (df['EMA24'].iloc[-1] > df['EMA50'].iloc[-1])
            cruce_bajista = (ema24_ant > ema50_ant) and (df['EMA24'].iloc[-1] < df['EMA50'].iloc[-1])
            
            
            # --- ANÁLISIS DE VOLUMEN ---
            # Compara el volumen de la vela de cruce (última cerrada) con la hora anterior (4 velas)
            vol_cruce = df['Volume'].iloc[-1]
            vol_hora_ant = df['Volume'].iloc[-5:-1].mean()
            vol_change_pct = ((vol_cruce - vol_hora_ant) / vol_hora_ant) * 100 if vol_hora_ant > 0 else 0
            
            # --- ANÁLISIS DE EMAs LENTAS ---
            ema_context = {
                'sobre_ema100': price_last_closed > ema100_last,
                'sobre_ema200': price_last_closed > ema200_last
            }
            
            
            # --- LONG (COMPRA): S1 < Precio < R1 ---
            if cruce_alcista and (price_last_closed > S1 and price_last_closed < R1):
                
                # --- ABRIR NUEVO TRADE LONG ---
                active_trades[symbol] = {
                    'status': 'OPEN',
                    'entry_type': 'LONG',
                    'entry_price': price_last_closed,
                    'tp1_key': 'R1', 'tp2_key': 'R2', 'sl_key': 'S1',
                    'tp1_hit': False, 'tp2_hit': False,
                    # Datos de análisis para documentación
                    'vol_pct_change': round(vol_change_pct, 2),
                    'ema_100_context': ema_context['sobre_ema100'],
                    'ema_200_context': ema_context['sobre_ema200'],
                    'entry_date': datetime.now().isoformat()
                }
                save_active_trades(active_trades)

                zona = f"S1 ({S1:.4f}) / R1 ({R1:.4f})"
                mensaje = f"🚀 *NUEVA COMPRA EN {symbol} (15M)*\n"
                mensaje += f"Cruce EMA24/50 ALCISTA.\n"
                mensaje += f"Precio Entrada: {price_last_closed:.4f} | PP: {PP:.4f}\n"
                mensaje += f"Volumen vs. Hora Anterior: **{vol_change_pct:.2f}%**\n"
                mensaje += f"Contexto EMA: 100({'✅' if ema_context['sobre_ema100'] else '❌'}) 200({'✅' if ema_context['sobre_ema200'] else '❌'})"
                enviar_telegram(mensaje)
                print(f"✅ NUEVA COMPRA detectada: {symbol}")
                
            # --- SHORT (VENTA): P < Precio < R2 ---
            elif cruce_bajista and (price_last_closed > PP and price_last_closed < R2):
                
                # --- ABRIR NUEVO TRADE SHORT ---
                active_trades[symbol] = {
                    'status': 'OPEN',
                    'entry_type': 'SHORT',
                    'entry_price': price_last_closed,
                    'tp1_key': 'PP', 'tp2_key': 'S1', 'sl_key': 'R2',
                    'tp1_hit': False, 'tp2_hit': False,
                    # Datos de análisis para documentación
                    'vol_pct_change': round(vol_change_pct, 2),
                    'ema_100_context': ema_context['sobre_ema100'],
                    'ema_200_context': ema_context['sobre_ema200'],
                    'entry_date': datetime.now().isoformat()
                }
                save_active_trades(active_trades)

                zona = f"PP ({PP:.4f}) / R2 ({R2:.4f})"
                mensaje = f"🔻 *NUEVA VENTA EN {symbol} (15M)*\n"
                mensaje += f"Cruce EMA24/50 BAJISTA.\n"
                mensaje += f"Precio Entrada: {price_last_closed:.4f} | PP: {PP:.4f}\n"
                mensaje += f"Volumen vs. Hora Anterior: **{vol_change_pct:.2f}%**\n"
                # Para Venta, el contexto de EMA debe ser el opuesto (bajo)
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
        
        # 1. Verificar y actualizar Pivotes (incluye resumen diario de trades cerrados)
        if not verificar_y_actualizar_pivotes():
            print("🛑 Fallo al actualizar Pivotes. Reintentando en el próximo ciclo.")
            
        # 2. Cargar los Pivotes del día
        try:
            with open(PIVOTS_FILE, 'r') as f:
                all_pivots = json.load(f)
        except Exception:
             all_pivots = {}

        # 3. Verificar si los trades activos ya tocaron TP o SL (gestión de riesgo y documentación de falla)
        check_active_trades(all_pivots)
        
        # 4. Detectar si hay nuevas señales (apertura de trade con análisis de volumen/EMA)
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
