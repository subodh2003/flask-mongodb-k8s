from flask import Flask, request, jsonify
from pymongo import MongoClient
from datetime import datetime
import os

app = Flask(__name__)

# URI (with credentials) is injected from a Kubernetes Secret.
# Locally: mongodb://admin:password123@localhost:27017/?authSource=admin
client = MongoClient(
    os.environ.get("MONGODB_URI", "mongodb://localhost:27017/"),
    serverSelectionTimeoutMS=5000,
)
db = client.flask_db
collection = db.data


@app.route("/")
def index():
    return f"Welcome to the Flask app! The current time is: {datetime.now()}"


@app.route("/data", methods=["GET", "POST"])
def data():
    if request.method == "POST":
        payload = request.get_json()
        collection.insert_one(payload)
        return jsonify({"status": "Data inserted"}), 201
    docs = list(collection.find({}, {"_id": 0}))
    return jsonify(docs), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
