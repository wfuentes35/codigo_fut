import json
from binance.client import Client
from dotenv import load_dotenv
import os

# Cargar variables de entorno del archivo .env
# Este comando debe estar al inicio para que las claves estén disponibles.
load_dotenv()

# ==============================================================================
# ⚠️ CONFIGURACIÓN DE LA API (Obtenida del .env)
# ==============================================================================
# Se usa os.getenv() para buscar las variables dentro del entorno cargado.
API_KEY = os.getenv("API_KEY")
SECRET_KEY = os.getenv("SECRET_KEY")

# Verifica si las claves existen antes de inicializar el cliente
if not API_KEY or not SECRET_KEY:
    raise ValueError("ERROR: API_KEY o SECRET_KEY no se encontraron en el archivo .env. Asegúrate de que el archivo existe y las variables están definidas.")

client = Client(API_KEY, SECRET_KEY)

def obtener_top_symbols(limit=200):
    """Obtiene los 'limit' mejores pares de USDT de Binance Futures."""
    try:
        # Usamos la función de futuros ya que tu monitoreo es en Futures.
        tickers = client.futures_ticker()
        
        # Filtra solo pares contra USDT y excluye índices
        usdt_pairs = [t for t in tickers if t['symbol'].endswith('USDT') and not t['symbol'].endswith('_USDT')]

        # Tomamos los primeros 'limit' pares
        top_symbols = [t['symbol'] for t in usdt_pairs[:limit]]

        if not top_symbols:
            print("❌ Error: No se encontraron pares USDT.")
            return []

        # Guardar en JSON
        with open('top_100_symbols.json', 'w') as f:
            json.dump(top_symbols, f)

        print(f"✅ Se guardaron {len(top_symbols)} pares en 'top_100_symbols.json'.")
        return top_symbols

    except Exception as e:
        print(f"❌ Error al obtener/guardar símbolos. Verifica la conexión a la API: {e}")
        return []

# Ejecutar el escaneo inicial
if __name__ == '__main__':
    obtener_top_symbols(limit=200)
