from app import flask_app, socketio

app = flask_app

if __name__ == "__main__":
    socketio.run(app, debug=True, host='0.0.0.0', port=3000)
