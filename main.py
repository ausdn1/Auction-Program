import random, sqlite3
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from contextlib import asynccontextmanager
from fastapi.responses import FileResponse

def get_db():
    conn = sqlite3.connect("auction_dice_final.db")
    conn.row_factory = sqlite3.Row
    return conn

@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS room (id INTEGER PRIMARY KEY, code TEXT, status TEXT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS leaders (id INTEGER PRIMARY KEY, name TEXT, points INTEGER DEFAULT 1000)")
    cursor.execute("CREATE TABLE IF NOT EXISTS team_members (leader_id INTEGER, member_name TEXT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS bids (leader_id INTEGER PRIMARY KEY, amount INTEGER, dice INTEGER DEFAULT 0)")
    cursor.execute("CREATE TABLE IF NOT EXISTS players_pool (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, is_sold INTEGER DEFAULT 0)")
    conn.commit(); conn.close()
    yield

app = FastAPI(lifespan=lifespan)

# --- 기본 API (방 생성, 입장, 상태 등은 이전과 동일) ---
class HostRoom(BaseModel): host_name: str; players: list
class JoinRoom(BaseModel): guest_name: str; code: str
class BidRequest(BaseModel): leader_id: int; amount: int

@app.get("/")
async def get_index(): return FileResponse("index.html")

@app.post("/create-room")
def create_room(data: HostRoom):
    code = str(random.randint(1000, 9999))
    conn = get_db()
    conn.execute("DELETE FROM room"); conn.execute("DELETE FROM leaders"); conn.execute("DELETE FROM players_pool"); conn.execute("DELETE FROM team_members"); conn.execute("DELETE FROM bids")
    conn.execute("INSERT INTO room (id, code, status) VALUES (1, ?, 'waiting')", (code,))
    conn.execute("INSERT INTO leaders (id, name, points) VALUES (1, ?, 1000)", (data.host_name,))
    for name in data.players: conn.execute("INSERT INTO players_pool (name) VALUES (?)", (name,))
    conn.commit(); conn.close()
    return {"code": code}

@app.post("/join-room")
def join_room(data: JoinRoom):
    conn = get_db()
    room = conn.execute("SELECT * FROM room WHERE id = 1 AND code = ?", (data.code,)).fetchone()
    if not room: conn.close(); raise HTTPException(status_code=400, detail="코드 불일치")
    conn.execute("INSERT OR REPLACE INTO leaders (id, name, points) VALUES (2, ?, 1000)", (data.guest_name,))
    conn.execute("UPDATE room SET status = 'playing' WHERE id = 1")
    conn.commit(); conn.close()
    return {"msg": "입장 성공"}

@app.get("/status")
def get_status():
    conn = get_db()
    room = conn.execute("SELECT * FROM room WHERE id = 1").fetchone()
    leaders = conn.execute("SELECT * FROM leaders").fetchall()
    bids = conn.execute("SELECT leader_id, amount, dice FROM bids").fetchall()
    p = conn.execute("SELECT * FROM players_pool WHERE is_sold = 0 ORDER BY id LIMIT 1").fetchone()
    teams = {1: [r['member_name'] for r in conn.execute("SELECT * FROM team_members WHERE leader_id=1")],
             2: [r['member_name'] for r in conn.execute("SELECT * FROM team_members WHERE leader_id=2")]}
    conn.close()
    return {"room": dict(room) if room else None, "leaders": [dict(r) for r in leaders], "bids": [dict(b) for b in bids], "teams": teams, "current_player": dict(p) if p else None}

@app.post("/bid")
def place_bid(bid: BidRequest):
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO bids (leader_id, amount, dice) VALUES (?, ?, 0)", (bid.leader_id, bid.amount))
    conn.commit(); conn.close()
    return {"msg": "입찰 완료"}

# --- 주사위 로직 수정 (1~6) ---
@app.post("/roll-dice")
def roll_dice(leader_id: int):
    val = random.randint(1, 6) # 1~6 사이로 변경
    conn = get_db()
    conn.execute("UPDATE bids SET dice = ? WHERE leader_id = ?", (val, leader_id))
    conn.commit(); conn.close()
    return {"dice": val}

@app.get("/reveal")
def reveal_result():
    conn = get_db()
    bids = conn.execute("SELECT b.*, l.name FROM bids b JOIN leaders l ON b.leader_id = l.id").fetchall()
    player = conn.execute("SELECT * FROM players_pool WHERE is_sold = 0 ORDER BY id LIMIT 1").fetchone()
    
    if len(bids) < 2 or not player: 
        conn.close(); return {"status": "waiting"}

    max_amt = max(r['amount'] for r in bids)
    top_bidders = [r for r in bids if r['amount'] == max_amt]

    # 입찰가 동점 상황
    if len(top_bidders) > 1:
        # 둘 다 주사위를 굴려야 함
        if any(r['dice'] == 0 for r in top_bidders):
            conn.close(); return {"status": "tie_break", "msg": "주사위를 굴려주세요."}
        
        # 주사위 숫자마저 동점인 경우
        if top_bidders[0]['dice'] == top_bidders[1]['dice']:
            # 주사위 값 초기화하여 다시 굴리게 유도
            conn.execute("UPDATE bids SET dice = 0")
            conn.commit(); conn.close()
            return {"status": "tie_break", "msg": "주사위 값이 같습니다! 다시 굴려주세요."}
        
        winner_row = max(top_bidders, key=lambda x: x['dice'])
        winner_id = winner_row['leader_id']
    else:
        winner_id = top_bidders[0]['leader_id']

    # 낙찰 처리
    conn.execute("UPDATE leaders SET points = points - ? WHERE id = ?", (max_amt, winner_id))
    conn.execute("INSERT INTO team_members (leader_id, member_name) VALUES (?, ?)", (winner_id, player['name']))
    conn.execute("UPDATE players_pool SET is_sold = 1 WHERE id = ?", (player['id'],))
    conn.execute("DELETE FROM bids"); conn.commit(); conn.close()
    return {"status": "success"}