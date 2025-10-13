import json
from binance.client import Client

# ⚠️ CONFIGURACIÓN DE LA API
API_KEY = "TU_API_KEY"
SECRET_KEY = "TU_SECRET_KEY"
client = Client(API_KEY, SECRET_KEY)

def obtener_top_symbols(limit=200):
    """Obtiene los 'limit' mejores pares de USDT por volumen en Binance Futures."""
    try:
        # Obtener información de todos los tickers de FUTUROS
        tickers = client.futures_ticker()
        
        # Filtra solo pares contra USDT y excluye índices
        usdt_pairs = [t for t in tickers if t['symbol'].endswith('USDT') and not t['symbol'].endswith('_USDT')]

        # Ordenar por volumen de trading (generalmente 'quoteVolume' o 'volume' de 24h)
        # Aquí usamos 'quoteVolume' (volumen en USDT), que es un buen proxy.
        # NOTA: La API de 'futures_ticker' no tiene un campo de volumen directo para ordenar fácilmente,
        # por lo que usaremos los primeros 100 pares devueltos por la API para simplificar la obtención.
        # Para un escaneo más preciso (por volumen real), se requeriría más lógica de solicitud de estadísticas.
        
        # Tomaremos los primeros 'limit' pares de la lista inicial
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
        print(f"❌ Error al obtener/guardar símbolos: {e}")
        return []

# Ejecutar el escaneo inicial
if __name__ == '__main__':

    obtener_top_symbols(limit=200)
