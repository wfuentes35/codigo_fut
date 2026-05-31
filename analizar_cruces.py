import pandas as pd
import numpy as np
import json
from binance.client import Client
from dotenv import load_dotenv
import os
from datetime import datetime, timezone
import traceback

# --- Configuración ---
load_dotenv()
API_KEY = os.getenv("API_KEY")
SECRET_KEY = os.getenv("SECRET_KEY")

if not API_KEY or not SECRET_KEY:
    print("ERROR: No se encontraron API_KEY o SECRET_KEY en el archivo .env.")
    print("Asegúrate de que el archivo .env está en el mismo directorio.")
    exit()

try:
    client = Client(API_KEY, SECRET_KEY, {"timeout": 60})
except Exception as e:
    print(f"Error al conectar con Binance (revisa tus claves API): {e}")
    exit()

TRADES_FILE = 'closed_trades.json'
BARS_TO_CHECK = 10  # Las 10 barras *después* de la entrada
EMA_SHORT = 24      # EMA rápida de tu bot
EMA_LONG = 50       # EMA lenta de tu bot

# --- Funciones de Indicadores (basadas en la lógica del bot) ---

def calculate_emas(df, short_span, long_span):
    """Calcula las EMAs en el DataFrame."""
    df['EMA_short'] = df['Close'].ewm(span=short_span, adjust=False).mean()
    df['EMA_long'] = df['Close'].ewm(span=long_span, adjust=False).mean()
    return df

def check_inverse_cross(df, entry_type):
    """
    Revisa si hay un cruce inverso en las 10 barras post-entrada.
    El DataFrame de entrada (df) debe tener 11 barras:
    - Fila 0: Barra de entrada (para usar como 'prev')
    - Filas 1-10: Las 10 barras post-entrada a chequear
    """
    inverse_cross_bar = -1
    
    # Empezamos en i=1 (la primera barra *después* de la entrada)
    for i in range(1, len(df)):
        prev = df.iloc[i-1]
        last = df.iloc[i]

        # Omitir si las EMAs no están listas (calentamiento)
        if pd.isna(prev['EMA_short']) or pd.isna(prev['EMA_long']) or \
           pd.isna(last['EMA_short']) or pd.isna(last['EMA_long']):
            continue 

        # Chequear cruce inverso para un LONG
        # (EMA_short cruza por DEBAJO de EMA_long)
        if entry_type == 'LONG':
            if (prev['EMA_short'] > prev['EMA_long']) and (last['EMA_short'] < last['EMA_long']):
                inverse_cross_bar = i # i es el número de barra (1-10)
                break
        
        # Chequear cruce inverso para un SHORT
        # (EMA_short cruza por ENCIMA de EMA_long)
        elif entry_type == 'SHORT':
            if (prev['EMA_short'] < prev['EMA_long']) and (last['EMA_short'] > last['EMA_long']):
                inverse_cross_bar = i # i es el número de barra (1-10)
                break
                
    return inverse_cross_bar

# --- Carga de Datos ---
try:
    with open(TRADES_FILE, 'r') as f:
        trades = json.load(f)
except FileNotFoundError:
    print(f"ERROR: No se encontró el archivo '{TRADES_FILE}'. Asegúrate de que esté en el mismo directorio.")
    exit()
except Exception as e:
    print(f"Error al cargar {TRADES_FILE}: {e}")
    exit()

print(f"Total de trades a analizar: {len(trades)}")

# Filtrar solo trades perdidos (CLOSED_SL)
lost_trades = [t for t in trades if t['status'] == 'CLOSED_SL']
print(f"Total de trades perdidos (SL): {len(lost_trades)}")

trades_with_inverse_cross = 0
trades_with_inverse_cross_pure_loss = 0
trades_with_inverse_cross_break_even = 0
total_pure_loss = 0
total_break_even = 0

analysis_results = []
processed_count = 0

for i, trade in enumerate(lost_trades):
    symbol = trade['symbol']
    entry_date_str = trade['entry_date']
    entry_type = trade['entry_type']
    is_break_even = trade.get('tp1_hit', False) # Usar .get() por seguridad
    
    if is_break_even:
        total_break_even += 1
    else:
        total_pure_loss += 1

    print(f"\n--- Analizando Trade {i+1}/{len(lost_trades)} ---")
    print(f"Símbolo: {symbol}, Tipo: {entry_type}, Entry: {entry_date_str}")

    try:
        # Convertir fecha de entrada a timestamp de Binance (milisegundos)
        entry_dt = datetime.fromisoformat(entry_date_str)
        entry_timestamp_ms = int(entry_dt.timestamp() * 1000)

        # Necesitamos 50 barras *antes* de la entrada para calentar las EMAs
        ms_15min = 900000 # 15 * 60 * 1000
        start_time_with_warmup = entry_timestamp_ms - (50 * ms_15min)
        
        # Pedimos 50 (warmup) + 1 (entrada) + 10 (chequeo) = 61 barras
        klines_with_warmup = client.futures_historical_klines(
            symbol=symbol,
            interval=Client.KLINE_INTERVAL_15MINUTE,
            start_str=str(start_time_with_warmup), # API prefiere string
            limit=50 + BARS_TO_CHECK + 1
        )

        if len(klines_with_warmup) < 52: # 50 warmup + 1 entrada + 1 post-barra
             print(f"No hay suficientes klines para {symbol} (con warmup). Se necesitan 52+, se obtuvieron {len(klines_with_warmup)}")
             continue
             
        df_warmup = pd.DataFrame(klines_with_warmup, columns=['open_time', 'Open', 'High', 'Low', 'Close', 'Volume', 'ct', 'qav','nt','tbbav','tbqav','ig'])
        df_warmup['Close'] = df_warmup['Close'].astype(float)
        
        # Calcular EMAs en todo el set de datos
        df_warmup = calculate_emas(df_warmup, EMA_SHORT, EMA_LONG)
        
        # La barra de entrada es el índice 50 (la 51ava barra).
        # Los 10 bares a chequear son del 51 al 60.
        
        # df_to_check debe incluir la barra de entrada (índice 50) para usarla como 'prev'
        # y las 10 barras siguientes (índice 51 a 60). Total 11 barras.
        # Slice: [50 : 50 + 10 + 1] -> [50:61]
        df_to_check = df_warmup.iloc[50 : 51 + BARS_TO_CHECK]
        
        if len(df_to_check) < BARS_TO_CHECK + 1:
             print(f"Datos insuficientes post-entrada para {symbol}. Se necesitan {BARS_TO_CHECK + 1} barras, se obtuvieron {len(df_to_check)}")
             continue

        # Chequear cruce inverso
        # Pasamos el df con 11 barras (entrada + 10 post)
        cross_bar_number = check_inverse_cross(df_to_check, entry_type)
        
        result_data = {
            "symbol": symbol,
            "entry_type": entry_type,
            "is_break_even": is_break_even,
            "inverse_cross_detected": cross_bar_number > -1,
            "bar_of_cross": cross_bar_number
        }
        analysis_results.append(result_data)
        processed_count += 1

        if cross_bar_number > -1:
            trades_with_inverse_cross += 1
            print(f"¡CRUCE INVERSO DETECTADO! en barra {cross_bar_number} post-entrada")
            if is_break_even:
                trades_with_inverse_cross_break_even += 1
            else:
                trades_with_inverse_cross_pure_loss += 1
        else:
            print("No se detectó cruce inverso.")

    except Exception as e:
        print(f"Error procesando {symbol}: {e}")
        # traceback.print_exc()

# --- Imprimir Resumen ---
print("\n\n--- RESUMEN DEL ANÁLISIS DE CRUCE INVERSO (en 10 barras) ---")
print(f"Total de Trades Perdidos (SL) analizados: {processed_count} de {len(lost_trades)}")
print(f"  - Derrotas Puras (sin TP1): {total_pure_loss}")
print(f"  - Empates (con TP1): {total_break_even}")

print("\n--- Resultados del Cruce Inverso ---")
if processed_count > 0:
    print(f"Trades Totales con Cruce Inverso: {trades_with_inverse_cross} de {processed_count} ({(trades_with_inverse_cross/processed_count)*100:.1f}%)")

    if total_pure_loss > 0:
        print(f"\nEn Derrotas Puras:")
        print(f"  {trades_with_inverse_cross_pure_loss} de {total_pure_loss} tuvieron un cruce inverso ({(trades_with_inverse_cross_pure_loss/total_pure_loss*100):.1f}%)")

    if total_break_even > 0:
        print(f"\nEn Empates (Break-Even):")
        print(f"  {trades_with_inverse_cross_break_even} de {total_break_even} tuvieron un cruce inverso ({(trades_with_inverse_cross_break_even/total_break_even*100):.1f}%)")
else:
    print("No se pudieron procesar trades para el análisis.")

# Guardar resultados detallados
try:
    with open('inverse_cross_analysis.json', 'w') as f:
        json.dump(analysis_results, f, indent=4)
    print("\nResultados detallados guardados en 'inverse_cross_analysis.json'")
except Exception as e:
    print(f"Error al guardar el JSON de análisis: {e}")
