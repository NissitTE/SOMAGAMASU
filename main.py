import os
from flask import Flask, render_template, request, jsonify
from scraper import SomagamasuScraper

app = Flask(__name__)
scraper = SomagamasuScraper()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/search', methods=['POST'])
def search():
    try:
        data = request.get_json()
        game_name = data.get('game_name', '').strip()
        lang      = data.get('lang', 'english').strip()

        if not game_name:
            return jsonify({"status": "error", "message": "Please enter a game name"}), 400

        results = scraper.fetch_deals(game_name, lang=lang)
        return jsonify({"status": "success", "data": results or [], "total": len(results or [])})

    except Exception as e:
        print(f"[server] Error: {e}")
        return jsonify({"status": "error", "message": "Server error, please try again"}), 500

if __name__ == '__main__':
    # ปรับให้ Render กำหนด Port เองได้
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
