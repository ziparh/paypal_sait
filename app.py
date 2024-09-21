from flask import Flask, render_template, redirect, url_for, request, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
import requests
import info
import base64
from datetime import datetime
import os
from werkzeug.utils import secure_filename
from PIL import Image, ImageDraw
import torch
import torchvision
from torchvision import transforms

app = Flask(__name__)

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///payments.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

UPLOAD_FOLDER = 'static/uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

db = SQLAlchemy(app)
migrate = Migrate(app, db)

class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    amount = db.Column(db.Float, nullable=False)
    percentage = db.Column(db.Integer, nullable=False)
    paypal_order_id = db.Column(db.String(120), nullable=False, unique=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<Payment {self.id} - ${self.amount} - {self.percentage}%>'

PAYPAL_API_URL = 'https://api-m.sandbox.paypal.com'

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
                    "value": f"{amount:.2f}"
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
            payment = Payment.query.filter_by(paypal_order_id=order_id).first()
            if payment:
                db.session.commit()
            return render_template('success.html', order=payment_details)
        else:
            return f"Ошибка завершения платежа: {response.text}"
    except Exception as e:
        return str(e)

@app.route('/cancel')
def cancel():
    return render_template('cancel.html')

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['GET', 'POST'])
def upload_image():
    if request.method == 'POST':
        if 'image' not in request.files:
            return "Нет файла в запросе", 400
        file = request.files['image']
        if file.filename == '':
            return "Файл не выбран", 400
        if file:
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)

            detections = detect_trash(filepath)

            return render_template('result.html', filename=filename, detections=detections)

    return render_template('upload.html')

def detect_trash(image_path):
    model = torchvision.models.detection.fasterrcnn_resnet50_fpn(pretrained=True)
    model.eval()

    transform = transforms.Compose([
        transforms.ToTensor(),
    ])

    image = Image.open(image_path).convert("RGB")
    image_tensor = transform(image)

    with torch.no_grad():
        predictions = model([image_tensor])

    detections = []
    for idx, (label, score, box) in enumerate(zip(predictions[0]['labels'], predictions[0]['scores'], predictions[0]['boxes'])):
        if score >= 0.5:
            label_name = torchvision.models.detection.FasterRCNN_ResNet50_FPN_Weights.DEFAULT.meta["categories"][label]
            if label_name in ['bottle', 'wine glass', 'cup',
    'fork', 'knife', 'spoon', 'bowl', 'banana', 'apple',
    'sandwich', 'orange', 'broccoli', 'carrot', 'hot dog',
    'pizza', 'donut', 'cake', 'cell phone', 'book']:
                detections.append({
                    'label': label_name,
                    'score': score.item(),
                    'box': box.tolist()
                })

    draw = ImageDraw.Draw(image)
    for det in detections:
        box = det['box']
        draw.rectangle(box, outline="red", width=3)
        draw.text((box[0], box[1]), f"{det['label']} {det['score']:.2f}", fill="red")

    result_filename = f"result_{os.path.basename(image_path)}"
    result_path = os.path.join(app.config['UPLOAD_FOLDER'], result_filename)
    image.save(result_path)

    return detections

@app.route('/static/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

if __name__ == '__main__':
    app.run(debug=True)