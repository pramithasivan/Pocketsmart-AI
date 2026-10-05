# PocketSmart AI: Your Smart Budget & Recommendation Assistant

PocketSmart AI is an intelligent financial planning and recommendation web application powered by **FastAPI** and **Google Gemini 1.5 Pro / Flash**. It offers three smart budget planners tailored for the Indian market:
1. **Home Interior Budget Planner**: Lighting, fans, furniture, dining sets, and decor with direct e-commerce search links (Amazon, Flipkart, IKEA, Myntra, Ajio).
2. **Party Budget Planner**: Event details, guest count, catering, venue, decoration, and entertainment suggestions.
3. **Jewelry Budget Planner**: Multimodal outfit photo analysis with outfit color extraction and jewelry matching across Tanishq, CaratLane, BlueStone, and Meesho.

---

## Features
- **FastAPI Backend**: Async endpoints, static file hosting, Jinja2 templating, and REST API routes.
- **JWT & Password Security**: Safe password hashing via Passlib (Bcrypt) and secure HTTP-only cookies.
- **Multimodal AI with Gemini**: Upload dresses/outfits to extract dominant colors and styles using `google-generativeai` and `Pillow`.
- **Indian Market Grounding**: Automatic dynamic generation of deep search links with URL encoded keywords.
- **User Dashboard & History**: Real-time tracking of previous recommendations.
- **Automatic Fallback Mode**: If no Gemini API key is configured, intelligent mock data is returned seamlessly so all UI features function out-of-the-box.

---

## Quickstart Guide

### 1. Extract the Project
Extract the zip file and navigate into the project directory:
```bash
cd PocketSmart_AI_Project
```

### 2. Create and Activate Virtual Environment
```bash
python -m venv venv
# On Windows:
venv\Scripts\activate
# On Linux / macOS:
source venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Open `.env` and add your Google Gemini API key:
```ini
GOOGLE_API_KEY=your_actual_gemini_api_key
SECRET_KEY=supersecretjwtkey_pocket_smart
```

### 5. Run the Server
```bash
python app.py
```
Or with Uvicorn directly:
```bash
uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

Open your browser and visit:
```text
http://127.0.0.1:8000
```
