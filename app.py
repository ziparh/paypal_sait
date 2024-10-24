from flask import Flask, render_template, redirect, url_for, request, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
import requests
import info
import base64
from datetime import datetime
import os


app = Flask(__name__)

# Настройка базы данных (используем SQLite)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///payments.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Настройка папки для загрузки изображений
UPLOAD_FOLDER = 'static/uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

db = SQLAlchemy(app)
migrate = Migrate(app, db)

# Модель для записи платежей
class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    amount = db.Column(db.Float, nullable=False)
    percentage = db.Column(db.Integer, nullable=False)
    paypal_order_id = db.Column(db.String(120), nullable=False, unique=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<Payment {self.id} - ${self.amount} - {self.percentage}%>'

# PayPal API данные (sandbox для тестирования)
PAYPAL_API_URL = 'https://api-m.sandbox.paypal.com'

# Получение токена для PayPal API
def get_paypal_token():
    url = "https://api.sandbox.paypal.com/v1/oauth2/token"
    payload = 'grant_type=client_credentials'
    encoded_auth = base64.b64encode((info.PAYPAL_CLIENT_ID + ':' + info.PAYPAL_SECRET_KEY).encode())
    headers = {
        'Authorization': f'Basic {encoded_auth.decode()}',
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    r = requests.post(url, headers=headers, data=payload)
    if r.status_code == 200:
        return r.json()["access_token"]
    else:
        raise Exception(f"Ошибка получения токена PayPal: {r.text}")

# Создание платежа
@app.route('/create-payment', methods=['POST'])
def create_payment():
    try:
        amount = float(request.form['amount'])
        percentage = int(request.form['percentage'])

        token = get_paypal_token()
        url = f"{PAYPAL_API_URL}/v2/checkout/orders"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        }

        payment_data = {
            "intent": "CAPTURE",
            "purchase_units": [{
                "amount": {
                    "currency_code": "USD",
                    "value": f"{amount:.2f}"  # Форматируем до двух знаков после запятой
                }
            }],
            "application_context": {
                "return_url": url_for('success', _external=True),
                "cancel_url": url_for('cancel', _external=True)
            }
        }

        response = requests.post(url, headers=headers, json=payment_data)
        if response.status_code == 201:
            payment = response.json()
            approval_url = next(link['href'] for link in payment['links'] if link['rel'] == 'approve')
            # Сохраняем предварительный платеж в базе данных
            new_payment = Payment(
                amount=amount,
                percentage=percentage,
                paypal_order_id=payment['id']
            )
            db.session.add(new_payment)
            db.session.commit()
            return redirect(approval_url)
        else:
            return f"Ошибка создания платежа: {response.text}"
    except Exception as e:
        return str(e)

# Маршрут для успешного завершения платежа
@app.route('/success')
def success():
    order_id = request.args.get('token')
    try:
        token = get_paypal_token()
        url = f"{PAYPAL_API_URL}/v2/checkout/orders/{order_id}/capture"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        }

        response = requests.post(url, headers=headers)
        if response.status_code == 201:
            payment_details = response.json()
            # Обновляем запись в базе данных
            payment = Payment.query.filter_by(paypal_order_id=order_id).first()
            if payment:
                db.session.commit()
            return render_template('success.html', order=payment_details)
        else:
            return f"Ошибка завершения платежа: {response.text}"
    except Exception as e:
        return str(e)

# Отмена платежа
@app.route('/cancel')
def cancel():
    return render_template('cancel.html')

# Главная страница
@app.route('/')
def index():
    return render_template('index.html')

if __name__ == '__main__':
    app.run(debug=True)