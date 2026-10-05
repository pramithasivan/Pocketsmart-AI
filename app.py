import os
import json
import re
import urllib.parse
import uuid
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

from fastapi import FastAPI, HTTPException, Depends, File, UploadFile, Form, Request, status
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, EmailStr
from passlib.context import CryptContext
from jose import JWTError, jwt
from PIL import Image
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("GOOGLE_API_KEY", "")
if API_KEY:
    genai.configure(api_key=API_KEY)

SECRET_KEY = os.getenv("SECRET_KEY", "pocket_smart_production_secret_key_123")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

app = FastAPI(title="PocketSmart: AI Budget Planner")

os.makedirs("static/uploads", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# --- In-Memory Stores ---
users_db: Dict[str, dict] = {}
user_recommendations: Dict[str, list] = {}
active_sessions: Dict[str, dict] = {}
blacklisted_tokens = set()

# --- Pydantic Models ---
class UserRegister(BaseModel):
    username: str
    email: EmailStr
    password: str

class HomeBudgetInput(BaseModel):
    total_budget: float
    num_lights: int = 0
    num_fans: int = 0
    num_furniture: int = 0
    num_dining_tables: int = 0
    has_living_room: bool = True
    has_kitchen: bool = False
    has_bedroom: bool = False
    additional_requirements: Optional[str] = "None"

class PartyBudgetInput(BaseModel):
    total_budget: float
    party_type: str
    num_guests: int
    venue_type: Optional[str] = "Home"
    needs_catering: bool = True
    needs_decoration: bool = True
    needs_entertainment: bool = True
    additional_requirements: Optional[str] = "None"

# --- Security / JWT Helpers ---
def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta if expires_delta else timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(request: Request) -> Optional[dict]:
    token = request.cookies.get("access_token")
    if not token or token in blacklisted_tokens:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            return None
        user = users_db.get(username)
        if user:
            # Update session activity
            if username in active_sessions:
                active_sessions[username]["last_activity"] = datetime.utcnow()
        return user
    except JWTError:
        return None

def extract_json_from_response(text: str) -> dict:
    try:
        # Match standard json blocks or raw json
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(1))
        # Fallback to outer braces
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end != 0:
            return json.loads(text[start:end])
        return {}
    except Exception:
        return {}

def mock_fallback_home(total_budget: float):
    return {
        "total_budget": total_budget,
        "remaining_budget": total_budget * 0.1,
        "budget_breakdown": [
            {
                "category": "Lighting",
                "allocation": total_budget * 0.2,
                "items": [
                    {"name": "Philips LED Smart Batten & Warm Bulbs", "description": "Energy efficient warm & daylight LED set", "estimated_price": total_budget * 0.2, "quantity": 4, "search_terms": "Philips LED smart light batten"}
                ]
            },
            {
                "category": "Ceiling Fans",
                "allocation": total_budget * 0.3,
                "items": [
                    {"name": "Atomberg BLDC Energy Saving Fans", "description": "High air delivery 1200mm inverter fans", "estimated_price": total_budget * 0.3, "quantity": 2, "search_terms": "Atomberg BLDC ceiling fan"}
                ]
            },
            {
                "category": "Furniture & Essentials",
                "allocation": total_budget * 0.4,
                "items": [
                    {"name": "Solid Sheesham Wood Compact Unit", "description": "Minimalist durable finish furniture", "estimated_price": total_budget * 0.4, "quantity": 1, "search_terms": "Sheesham wood compact table unit"}
                ]
            }
        ],
        "additional_suggestions": [
            "Opt for BLDC fans to save up to 65% power consumption.",
            "Compare Flipkart and Amazon India festive offers for additional bank cashback."
        ]
    }

def get_home_recommendations(b_input: HomeBudgetInput) -> dict:
    if not API_KEY or API_KEY == "your_gemini_api_key_here":
        result = mock_fallback_home(b_input.total_budget)
    else:
        prompt = f"""
        Generate interior design budget recommendations in INR (₹) for an Indian home.
        Total Budget: ₹{b_input.total_budget:.2f}
        Requirements:
        - {b_input.num_lights} lights/fixtures
        - {b_input.num_fans} ceiling fans
        - {b_input.num_furniture} furniture pieces
        - {b_input.num_dining_tables} dining tables
        Rooms: Living Room={b_input.has_living_room}, Kitchen={b_input.has_kitchen}, Bedroom={b_input.has_bedroom}
        Extra notes: {b_input.additional_requirements}

        Respond ONLY with a JSON object matching this exact schema:
        {{
          "total_budget": {b_input.total_budget:.2f},
          "budget_breakdown": [
            {{
              "category": "Lighting",
              "allocation": 0.0,
              "items": [
                {{
                  "name": "Item Name",
                  "description": "Short description",
                  "estimated_price": 0.0,
                  "quantity": 1,
                  "search_terms": "item keywords"
                }}
              ]
            }}
          ],
          "remaining_budget": 0.0,
          "additional_suggestions": ["Suggestion 1", "Suggestion 2"]
        }}
        """
        try:
            model = genai.GenerativeModel("gemini-1.5-flash")
            response = model.generate_content(prompt)
            result = extract_json_from_response(response.text)
            if not result or "budget_breakdown" not in result:
                result = mock_fallback_home(b_input.total_budget)
        except Exception:
            result = mock_fallback_home(b_input.total_budget)

    # Attach shopping query links
    for cat in result.get("budget_breakdown", []):
        for item in cat.get("items", []):
            st = item.get("search_terms") or item.get("name", "")
            q = urllib.parse.quote_plus(st)
            item["shopping_links"] = {
                "amazon": f"https://www.amazon.in/s?k={q}",
                "flipkart": f"https://www.flipkart.com/search?q={q}",
                "ikea": f"https://www.ikea.com/in/en/search/?q={q}",
                "myntra": f"https://www.myntra.com/search?q={q}",
                "ajio": f"https://www.ajio.com/search/?text={q}"
            }
    return result

def get_party_recommendations(b_input: PartyBudgetInput) -> dict:
    if not API_KEY or API_KEY == "your_gemini_api_key_here":
        result = {
            "total_budget": b_input.total_budget,
            "remaining_budget": b_input.total_budget * 0.05,
            "budget_breakdown": [
                {
                    "category": "Catering & Refreshments",
                    "allocation": b_input.total_budget * 0.5,
                    "items": [
                        {"name": f"Party Meal Combo for {b_input.num_guests} guests", "description": "Starters, Main course and beverages", "estimated_price": b_input.total_budget * 0.5, "quantity": b_input.num_guests, "search_terms": "Swiggy party catering bulk meal"}
                    ]
                },
                {
                    "category": "Decoration",
                    "allocation": b_input.total_budget * 0.25,
                    "items": [
                        {"name": "DIY Balloon Arch & Fairy Light Kit", "description": "Complete birthday/party festive backdrop setup", "estimated_price": b_input.total_budget * 0.25, "quantity": 1, "search_terms": "party backdrop balloon decoration kit"}
                    ]
                },
                {
                    "category": "Entertainment & Music",
                    "allocation": b_input.total_budget * 0.2,
                    "items": [
                        {"name": "Party Speaker & Party Board Games", "description": "Bluetooth portable party speaker combo", "estimated_price": b_input.total_budget * 0.2, "quantity": 1, "search_terms": "party board games bluetooth speaker"}
                    ]
                }
            ],
            "venue_suggestions": [
                {"name": f"{b_input.venue_type} (Recommended)", "type": b_input.venue_type, "capacity": b_input.num_guests, "estimated_cost": 0.0, "search_terms": f"party hall near me {b_input.venue_type}"}
            ],
            "additional_suggestions": [
                "Consider DIY decorations to save up to 40% on event styling.",
                "Order snacks in bulk via Swiggy or Zomato corporate orders."
            ]
        }
    else:
        prompt = f"""
        Generate party planning budget recommendations in INR (₹) for India.
        Total Budget: ₹{b_input.total_budget:.2f}
        Party Type: {b_input.party_type}
        Number of Guests: {b_input.num_guests}
        Venue: {b_input.venue_type}
        Catering: {b_input.needs_catering}
        Decorations: {b_input.needs_decoration}
        Entertainment: {b_input.needs_entertainment}

        Respond ONLY with a JSON object:
        {{
          "total_budget": {b_input.total_budget:.2f},
          "budget_breakdown": [
            {{
              "category": "catering",
              "allocation": 0.0,
              "items": [
                {{
                  "name": "Service/Item name",
                  "description": "Details",
                  "estimated_price": 0.0,
                  "quantity": 1,
                  "search_terms": "keywords"
                }}
              ]
            }}
          ],
          "venue_suggestions": [
            {{
              "name": "Suggested venue",
              "type": "{b_input.venue_type}",
              "capacity": {b_input.num_guests},
              "estimated_cost": 0.0,
              "search_terms": "venue location keywords"
            }}
          ],
          "remaining_budget": 0.0,
          "additional_suggestions": ["Suggestion 1", "Suggestion 2"]
        }}
        """
        try:
            model = genai.GenerativeModel("gemini-1.5-flash")
            response = model.generate_content(prompt)
            result = extract_json_from_response(response.text)
            if not result or "budget_breakdown" not in result:
                result = mock_fallback_home(b_input.total_budget)
        except Exception:
            result = mock_fallback_home(b_input.total_budget)

    # Attach links
    for cat in result.get("budget_breakdown", []):
        for item in cat.get("items", []):
            st = item.get("search_terms") or item.get("name", "")
            q = urllib.parse.quote_plus(st)
            item["shopping_links"] = {
                "amazon": f"https://www.amazon.in/s?k={q}",
                "flipkart": f"https://www.flipkart.com/search?q={q}",
                "swiggy": f"https://www.swiggy.com/search?query={q}",
                "zomato": f"https://www.zomato.com/search?q={q}",
                "bookmyshow": f"https://in.bookmyshow.com/search?q={q}"
            }
    return result

def get_jewelry_recommendations(budget: float, occasion: str, preferences: str, image_path: Optional[str] = None) -> dict:
    if not API_KEY or API_KEY == "your_gemini_api_key_here":
        result = {
            "outfit_analysis": {
                "colors": ["Royal Blue", "Gold accents"],
                "style": "Contemporary Ethnic",
                "formality": "Festive / Celebration"
            },
            "total_budget": budget,
            "remaining_budget": budget * 0.15,
            "jewelry_recommendations": [
                {
                    "item_type": "Necklace Set",
                    "name": "Kundan & Pearl Choker Necklace",
                    "description": "Elegant Kundan work choker with matching drop earrings",
                    "style": "Traditional Indian / Festive",
                    "estimated_price": budget * 0.55,
                    "search_terms": f"Kundan choker necklace {occasion}"
                },
                {
                    "item_type": "Bangles / Bracelet",
                    "name": "Gold-Plated Temple Kada Pair",
                    "description": "Handcrafted embossed floral motif kadas",
                    "style": "Classic Antique",
                    "estimated_price": budget * 0.3,
                    "search_terms": "Gold plated kadas temple jewelry"
                }
            ],
            "styling_tips": [
                "Pair this choker with an open sweetheart or deep scoop neckline.",
                "Complement with neat hair updo to highlight drop earrings."
            ]
        }
    else:
        model = genai.GenerativeModel("gemini-1.5-flash")
        prompt = f"""
        Act as an expert Indian fashion stylist & jewelry consultant.
        Total Budget: ₹{budget:.2f}
        Occasion: {occasion}
        Preferences: {preferences}

        Analyze the outfit (if image provided) and recommend complementary jewelry available in India within budget.
        Respond ONLY with a JSON object:
        {{
          "outfit_analysis": {{
            "colors": ["Detected Color 1"],
            "style": "Ethnic/Western/Indo-Western",
            "formality": "Casual/Festive/Formal"
          }},
          "total_budget": {budget:.2f},
          "jewelry_recommendations": [
            {{
              "item_type": "Necklace/Earrings/Bangles",
              "name": "Product Name",
              "description": "Design styling details",
              "style": "Kundan/Polki/Silver/Modern",
              "estimated_price": 0.0,
              "search_terms": "shopping search phrase"
            }}
          ],
          "remaining_budget": 0.0,
          "styling_tips": ["Styling advice 1", "Styling advice 2"]
        }}
        """
        try:
            if image_path and os.path.exists(image_path):
                img = Image.open(image_path)
                response = model.generate_content([prompt, img])
            else:
                response = model.generate_content(prompt)
            result = extract_json_from_response(response.text)
            if not result or "jewelry_recommendations" not in result:
                result = {"jewelry_recommendations": []}
        except Exception:
            result = {"jewelry_recommendations": []}

    # Add jewelry specific shopping links
    for item in result.get("jewelry_recommendations", []):
        st = item.get("search_terms") or item.get("name", "")
        q = urllib.parse.quote_plus(st)
        item["shopping_links"] = {
            "amazon": f"https://www.amazon.in/s?k={q}",
            "flipkart": f"https://www.flipkart.com/search?q={q}",
            "tanishq": f"https://www.tanishq.co.in/search?q={q}",
            "caratlane": f"https://www.caratlane.com/search?q={q}",
            "bluestone": f"https://www.bluestone.com/search.html?query={q}",
            "meesho": f"https://www.meesho.com/search?q={q}"
        }
    return result

# --- Routes ---
@app.get("/", response_class=HTMLResponse)
async def home_page(request: Request):
    user = await get_current_user(request)
    return templates.TemplateResponse("index.html", {"request": request, "user": user})

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user = await get_current_user(request)
    if user:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse("login.html", {"request": request, "user": None})

@app.post("/login")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    user = users_db.get(username)
    if not user or not verify_password(password, user["hashed_password"]):
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid username or password", "user": None})
    
    access_token = create_access_token(data={"sub": username})
    active_sessions[username] = {
        "username": username,
        "login_time": datetime.utcnow(),
        "last_activity": datetime.utcnow(),
        "token": access_token,
        "user_data": {}
    }
    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    response.set_cookie(key="access_token", value=access_token, httponly=True, max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60, samesite="lax")
    return response

@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    user = await get_current_user(request)
    if user:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse("register.html", {"request": request, "user": None})

@app.post("/register")
async def register_submit(request: Request, username: str = Form(...), email: str = Form(...), password: str = Form(...), confirm_password: str = Form(...)):
    if password != confirm_password:
        return templates.TemplateResponse("register.html", {"request": request, "error": "Passwords do not match", "user": None})
    if username in users_db:
        return templates.TemplateResponse("register.html", {"request": request, "error": "Username already taken", "user": None})
    
    users_db[username] = {
        "username": username,
        "email": email,
        "hashed_password": get_password_hash(password)
    }
    return RedirectResponse(url="/login?registered=true", status_code=status.HTTP_302_FOUND)

@app.get("/logout")
async def logout(request: Request):
    token = request.cookies.get("access_token")
    if token:
        blacklisted_tokens.add(token)
    response = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    response.delete_cookie(key="access_token")
    return response

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = await get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    user_recs = user_recommendations.get(user["username"], [])
    return templates.TemplateResponse("dashboard.html", {"request": request, "user": user, "recent_history": user_recs[:5]})

@app.get("/home-planner", response_class=HTMLResponse)
async def home_planner_page(request: Request):
    user = await get_current_user(request)
    return templates.TemplateResponse("home_planner.html", {"request": request, "user": user})

@app.post("/api/home-budget")
async def plan_home_budget(budget_input: HomeBudgetInput, request: Request):
    user = await get_current_user(request)
    result = get_home_recommendations(budget_input)
    if user:
        rec_entry = {
            "id": str(uuid.uuid4()),
            "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "type": "Home Interior",
            "input_summary": f"₹{budget_input.total_budget:,.2f} Budget | {budget_input.num_lights} Lights, {budget_input.num_fans} Fans",
            "full_result": result
        }
        user_recommendations.setdefault(user["username"], []).insert(0, rec_entry)
    return JSONResponse(result)

@app.get("/party-planner", response_class=HTMLResponse)
async def party_planner_page(request: Request):
    user = await get_current_user(request)
    return templates.TemplateResponse("party_planner.html", {"request": request, "user": user})

@app.post("/api/party-budget")
async def plan_party_budget(budget_input: PartyBudgetInput, request: Request):
    user = await get_current_user(request)
    result = get_party_recommendations(budget_input)
    if user:
        rec_entry = {
            "id": str(uuid.uuid4()),
            "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "type": "Party & Event",
            "input_summary": f"₹{budget_input.total_budget:,.2f} Budget | {budget_input.party_type} ({budget_input.num_guests} guests)",
            "full_result": result
        }
        user_recommendations.setdefault(user["username"], []).insert(0, rec_entry)
    return JSONResponse(result)

@app.get("/jewelry-planner", response_class=HTMLResponse)
async def jewelry_planner_page(request: Request):
    user = await get_current_user(request)
    return templates.TemplateResponse("jewelry_planner.html", {"request": request, "user": user})

@app.post("/api/jewelry-budget")
async def plan_jewelry_budget(
    request: Request,
    total_budget: float = Form(...),
    occasion: str = Form(...),
    preferences: Optional[str] = Form(None),
    image: Optional[UploadFile] = File(None)
):
    user = await get_current_user(request)
    image_path = None
    if image and image.filename:
        filename = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{image.filename}"
        image_path = os.path.join("static", "uploads", filename)
        with open(image_path, "wb") as f_out:
            f_out.write(await image.read())

    result = get_jewelry_recommendations(total_budget, occasion, preferences or "Not specified", image_path)
    if user:
        rec_entry = {
            "id": str(uuid.uuid4()),
            "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "type": "Jewelry Styling",
            "input_summary": f"₹{total_budget:,.2f} Budget | {occasion}",
            "full_result": result
        }
        user_recommendations.setdefault(user["username"], []).insert(0, rec_entry)
    return JSONResponse(result)

@app.get("/history", response_class=HTMLResponse)
async def history_page(request: Request):
    user = await get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    history = user_recommendations.get(user["username"], [])
    return templates.TemplateResponse("history.html", {"request": request, "user": user, "history": history})

if __name__ == "__main__":
    import uvicorn
    print("Starting PocketSmart: AI Budget & Recommendation Assistant...")
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
