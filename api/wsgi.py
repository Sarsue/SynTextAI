from app import flask_app
from websocket_server import socketio
from kombu.utils.url import safequote
import os
from dotenv import load_dotenv
load_dotenv()
app = flask_app

redis_username = os.getenv('REDIS_USERNAME')
redis_pwd = os.getenv('REDIS_PASSWORD')
redis_host = os.getenv('REDIS_HOST')
redis_port = os.getenv('REDIS_PORT')
redis_url = (
    f"rediss://:{safequote(redis_pwd)}@{redis_host}:{redis_port}/0"
    "?ssl_cert_reqs=CERT_REQUIRED&ssl_ca_certs=config/ca-certificate.crt"
)

if __name__ == "__main__":
   # socketio.run(app, debug=True, host='0.0.0.0', port=3000)
    socketio.init_app(app, message_queue=redis_url)
    socketio.run(flask_app, debug=True, host='0.0.0.0', port=3000)

