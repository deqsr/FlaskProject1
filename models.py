from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class Station(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False) # Наприклад, "Kyiv" (для API запиту)
    display_name = db.Column(db.String(150), nullable=False)    # Наприклад, "Kyiv Central Station"
    country = db.Column(db.String(50), default="Україна")
    # Можна додати lat/lon, якщо WAQI API буде використовувати їх для конкретної станції,
    # але для feed/{city}/ часто це не потрібно.
    # Для WAQI часто краще використовувати ID станції, якщо відомий, або назву міста/регіону.
    # Для простоти, поки що будемо шукати за назвою міста.

    readings = db.relationship('AirReading', backref='station', lazy=True, cascade="all, delete-orphan")

    def __repr__(self):
        return f'<Station {self.display_name}>'

class AirReading(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    station_id = db.Column(db.Integer, db.ForeignKey('station.id'), nullable=False)
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow) # Час запису в БД
    data_time = db.Column(db.DateTime, nullable=True) # Час, коли дані були виміряні (з API)

    aqi = db.Column(db.Integer, nullable=True)
    pm25 = db.Column(db.Float, nullable=True)
    pm10 = db.Column(db.Float, nullable=True)
    o3 = db.Column(db.Float, nullable=True)
    no2 = db.Column(db.Float, nullable=True)
    so2 = db.Column(db.Float, nullable=True)
    co = db.Column(db.Float, nullable=True)
    # Інші показники за потреби: t (температура), p (тиск), h (вологість), w (вітер)

    def __repr__(self):
        return f'<AirReading {self.station.name} AQI: {self.aqi} at {self.timestamp}>'