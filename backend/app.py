import os
import json
import requests
import btcde
import mysql.connector
from decimal import Decimal
from flask import Flask, request, jsonify
from flask_socketio import SocketIO
from flask_cors import CORS
from datetime import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)
socketio = SocketIO(app, cors_allowed_origins="*")

# MySQL Configuration
DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "root",
    "database": "btc_track"
}

def get_db_connection():
    return mysql.connector.connect(**DB_CONFIG)

check_arbitrage_running = False

api_key = os.getenv("BITCOIN_DE_API_KEY", "API_KEY")
api_secret = os.getenv("BITCOIN_DE_API_SECRET", "API_SECRET")

class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, datetime):
            return obj.isoformat() 
        return super().default(obj)

def send_email(order_id, profit, min_price, fee):
    # Create a MIMEText object to represent the email message
    sender_email = "sender@gmail.com"
    receiver_email = "receiver@gmail.co,"
    smtp_server = "smtp.zoho.com"  # Change based on your email provider
    smtp_port = 465
    smtp_username = "carlajesusfree@gmail.com"
    smtp_password = "G71C000B4110a!"  # Use App Password if using Gmail

    subject = "High Profit Alert!"
    body = f"""
    A new opportunity has been found!

    Order ID: {order_id}
    Profit: ${profit}
    Fee: {fee}
    Minimum Required Profit: ${min_price}

    This profit exceeds the minimum threshold.
    """

    msg = MIMEMultipart()
    msg["From"] = sender_email
    msg["To"] = receiver_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    # Connect to the SMTP server
    with smtplib.SMTP(smtp_server, smtp_port) as server:
        # Start TLS encryption
        server.starttls()
        # Login to the SMTP server
        server.login(smtp_username, smtp_password)
        server.sendmail(sender_email, receiver_email, msg.as_string())


def get_bitcoin_de_offers():
    try:
        conn = btcde.Connection(api_key, api_secret)
        order_type = {
            "order_requirements_fullfilled": 1,
            "only_kyc_full": 1,
            "payment_option": 2
        }
        orderbook = conn.showOrderbook('buy', 'btceur', **order_type)
        return orderbook.get('orders', [])
    except requests.RequestException as e:
        print(f"[ERROR] Bitcoin.de API request failed: {e}")
        return []
    except Exception as e:
        print(f"[ERROR] Unexpected error fetching Bitcoin.de offers: {e}")
        return []

def get_kraken_price():
    try:
        response = requests.get("https://api.kraken.com/0/public/Ticker?pair=XXBTZEUR", timeout=5)
        response.raise_for_status()
        data = response.json()
        return float(data["result"]["XXBTZEUR"]["c"][0])
    except requests.RequestException as e:
        print(f"[ERROR] Kraken API request failed: {e}")
    except KeyError:
        print(f"[ERROR] Unexpected Kraken API response format.")
    return None

def check_arbitrage():
    global check_arbitrage_running
    while True:
        try:
            bitcoin_de_offers = get_bitcoin_de_offers()
            kraken_price = get_kraken_price()
            data = {
                "bitcoin_de_offers": bitcoin_de_offers,
                "kraken_price": kraken_price
            }
            encoded_data = json.dumps(data, cls=DecimalEncoder)
            socketio.emit("arbitrage_update", encoded_data)
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM opportunities ORDER BY id DESC")
            rows = cursor.fetchall()
            cursor.close()
            conn.close()
            socketio.emit("saved_opportunities", json.dumps(rows, cls=DecimalEncoder))

            socketio.sleep(10)
        except Exception as e:
            print(f"Error in check_arbitrage: {e}")

@socketio.on("connect")
def handle_connect():
    global check_arbitrage_running
    print("[INFO] Client connected")
    if not check_arbitrage_running:
        check_arbitrage_running = True
        socketio.start_background_task(check_arbitrage)

@app.route("/opportunities", methods=["POST"])
def save_opportunity():
    try:
        data = request.json
        conn = get_db_connection()
        cursor = conn.cursor()

        # Extract and convert values
        quantity = data['quantity']
        quantity_min = data['quantity_min']
        price = int(data['price'])
        kraken = int(data['kraken'])
        volume = int(data['volume'])
        profit = int(data['profit'])
        order_id = data['order_id']
        fee = data['fee']

        # Check if record with the same order_id, fee, and profit already exists
        check_query = """
            SELECT COUNT(*) FROM opportunities 
            WHERE order_id = %s AND fee = %s;
        """
        cursor.execute(check_query, (order_id, fee))
        result = cursor.fetchone()

        # If record exists, don't insert and return message
        if result[0] > 0:
            return jsonify({"message": "Record with the same order_id, fee, and profit already exists"}), 409

        cursor.execute("SELECT minPrice FROM settings LIMIT 1")
        min_price_row = cursor.fetchone()
        min_price = float(min_price_row[0]) if min_price_row else 0.0
        if profit / 100.0 > min_price:
            send_email(order_id, profit, min_price, fee)

        # If record does not exist, insert the new data
        insert_query = """
            INSERT INTO opportunities (order_id, quantity, quantity_min, price, kraken, volume, profit, fee)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """
        cursor.execute(insert_query, (order_id, quantity, quantity_min, price, kraken, volume, profit, fee))
        conn.commit()

        cursor.close()
        conn.close()
        return jsonify({"message": "Data saved successfully"}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/minprice", methods=["POST"])
def set_min_price():
    try:
        data = request.json
        conn = get_db_connection()
        cursor = conn.cursor()

        # Extract and convert value
        minPrice = float(data['minPrice'])

        # update query
        update_query = """
            UPDATE `settings` 
            SET `minPrice` = %s 
            WHERE `id` = 1;
        """
        cursor.execute(update_query, (minPrice,))
        conn.commit()

        cursor.close()
        conn.close()
        return jsonify({"message": "Minium Price updated"}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/minprice", methods=["GET"])
def get_min_price():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM settings")
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return jsonify(rows)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/opportunities", methods=["GET"])
def get_opportunities():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM opportunities ORDER BY id DESC")
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return jsonify(rows)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", debug=False, allow_unsafe_werkzeug=True)