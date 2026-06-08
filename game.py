#!/usr/bin/env python3
"""
Fish & Chips & Shipwreck — Multiplayer Strategy Game
Single-file implementation for classroom use.

Setup:
    pip install flask flask-socketio

Run:
    python game.py

Then:
    Teacher opens http://<local-ip>:5000 on the big screen
    Players scan QR code or visit http://<local-ip>:5000/join
"""

import sys
import os
import random
import math
import time
import socket
import json
import uuid
import threading
import urllib.request

try:
    from flask import Flask, request, Response, jsonify
    from flask_socketio import SocketIO, emit
except ImportError:
    print("Please install dependencies: pip install flask flask-socketio eventlet")
    sys.exit(1)

SOCKETIO_JS_CACHE = None

# ============================================================
# CONSTANTS
# ============================================================

DAY_DURATION = 120
INITIAL_LAKE_FISH = 5000
LAKE_CARRYING_CAPACITY = 8000
LAKE_GROWTH_RATE = 0.3
DEPLETION_THRESHOLD = 300
INITIAL_SATIETY = 5
INITIAL_FISH = 5
INITIAL_GOLD = 20
MAX_SATIETY = 5
MAX_EAT_PER_DAY = 5
SELL_PRICE_BASE = 2
FISH_ROT_RATE = 0.1
MAX_TRADE_ORDERS = 3
INTERLUDE_SECONDS = 10
FISHING_DURATION = 20

BOAT_STATS = {
    1: {"name": "Raft", "clicks_per_fish": 8, "attack": 10, "defense": 5, "upgrade_cost": 0, "trip_cost": 5},
    2: {"name": "Sailboat", "clicks_per_fish": 4, "attack": 20, "defense": 12, "upgrade_cost": 80, "trip_cost": 10},
    3: {"name": "Motorboat", "clicks_per_fish": 2, "attack": 35, "defense": 25, "upgrade_cost": 200, "trip_cost": 25},
    4: {"name": "Armed Trawler", "clicks_per_fish": 1, "attack": 60, "defense": 40, "upgrade_cost": 500, "trip_cost": 50},
}


WORLD_EVENTS = [
    {"id": "fish_migration", "name": "Fish Migration", "icon": "\U0001f30a",
     "desc": "Lake fish stock decreases by 100", "effect": "lake_fish_minus_100", "color": "#e8f0f8"},
    {"id": "fish_boom", "name": "Fish Boom", "icon": "\U0001f41f",
     "desc": "All players' catch amount doubled", "effect": "catch_double", "color": "#e8f5e8"},
    {"id": "storm", "name": "Storm Warning", "icon": "⛈️",
     "desc": "Clicks needed per fish ×1.5", "effect": "fishing_time_plus_50", "color": "#ece8f0"},
    {"id": "harvest", "name": "Harvest Festival", "icon": "\U0001f3a3",
     "desc": "Eating fish restores double satiety (+2 per fish)", "effect": "eat_double", "color": "#fdf5e8"},
    {"id": "pirate_alert", "name": "Pirate Alert", "icon": "\U0001f3f4‍☠️",
     "desc": "Raid success rate halved", "effect": "attack_rate_half", "color": "#f8e8e8"},
    {"id": "eco_subsidy", "name": "Eco-Subsidy", "icon": "\U0001f33f",
     "desc": "Catch <10 fish today → earn 15 gold bonus at day end", "effect": "low_catch_bonus", "color": "#e8f5f0"},
    {"id": "fishing_ban", "name": "Fishing Ban", "icon": "\U0001f512",
     "desc": "Clicks needed per fish ×2, but receive 20 gold subsidy", "effect": "fishing_time_double_20_gold", "color": "#fdf0e0"},
    {"id": "price_spike", "name": "Price Swing", "icon": "\U0001f4b0",
     "desc": "Sell price to system: 2→5 gold", "effect": "price_change", "color": "#fdf8e0"},
]

POSITIVE_EVENTS = [
    {"id": "lucky_day", "name": "Lucky Day", "icon": "\U0001f340",
     "desc": "First fishing trip today yields +50% catch", "type": "today", "effect": "first_catch_bonus_50"},
    {"id": "energetic", "name": "Energetic", "icon": "\U0001f4aa",
     "desc": "Satiety decay halved today (lose 1 instead of 2)", "type": "today", "effect": "satiety_decay_half"},
    {"id": "bonus_fish", "name": "Windfall", "icon": "\U0001f3a3",
     "desc": "Gain 10 fish immediately", "type": "instant", "effect": "gain_10_fish"},
    {"id": "bonus_gold", "name": "Found Gold", "icon": "\U0001f4b0",
     "desc": "Gain 20 gold immediately", "type": "instant", "effect": "gain_20_gold"},
    {"id": "quick_repair", "name": "Quick Repair", "icon": "⚡",
     "desc": "One free automatic boat repair when damaged", "type": "consumable", "effect": "free_repair"},
]

NEUTRAL_EVENTS = []

NEGATIVE_EVENTS = [
    {"id": "fatigue", "name": "Fatigue", "icon": "\U0001f62b",
     "desc": "Catch amount halved today", "type": "today", "effect": "catch_half"},
    {"id": "fish_disease", "name": "Fish Rot", "icon": "\U0001fda0",
     "desc": "Start of day: fish stock rots 30% instead of 10%", "type": "today", "effect": "rot_30_percent"},
    {"id": "net_broken", "name": "Torn Net", "icon": "\U0001f4b8",
     "desc": "Pay 10 gold repair fee (go into debt if insufficient)", "type": "instant", "effect": "pay_10_gold"},
    {"id": "pirate_target", "name": "Pirate Target", "icon": "\U0001f3f4‍☠️",
     "desc": "When raided today, attacker's success rate +20%", "type": "today", "effect": "defense_penalty_20"},
    {"id": "hull_leak", "name": "Hull Leak", "icon": "\U0001f30a",
     "desc": "Clicks needed per fish ×1.5 today", "type": "today", "effect": "fishing_time_plus_50_personal"},
    {"id": "food_poison", "name": "Food Poisoning", "icon": "\U0001f637",
     "desc": "Lose 2 satiety immediately", "type": "instant", "effect": "satiety_minus_2"},
]

# ============================================================
# HELPERS
# ============================================================

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0)
        s.connect(("10.254.254.254", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def make_player_state():
    return {
        "sid": "",
        "name": "",
        "satiety": INITIAL_SATIETY,
        "fish": INITIAL_FISH,
        "gold": INITIAL_GOLD,
        "boat_level": 1,
        "is_fishing": False,
        "fishing_start": 0,
        "fishing_duration": FISHING_DURATION,
        "fishing_end_at": 0,
        "fishing_clicks": 0,
        "busy_until": 0,
        "busy_action": "",
        "attack_cooldown_until": 0,
                "personal_event": None,
        "eat_count_today": 0,
        "first_fish_done": False,
        "daily_catch": 0,
        "daily_modifiers": {},
        "consumables": [],
        "eliminated": False,
        "connected": True,
    }

# ============================================================
# GAME STATE
# ============================================================

class GameState:
    def __init__(self):
        self.players = {}
        self.world = {
            "day": 0,
            "time_remaining": DAY_DURATION,
            "lake_fish": INITIAL_LAKE_FISH,
            "current_event": None,
            "phase": "lobby",
            "time_speed": 1.0,
            "satiety_milestones": [],  # time_remaining thresholds to trigger
        }
        self.trade_orders = []
        self.notifications = []
        self.tick_task = None
        self.lock = threading.RLock()
        self._pending_next_day = False

    # ---------- player management ----------

    def add_player(self, sid, name):
        with self.lock:
            if self.world["phase"] != "lobby":
                return None
            existing = None
            for s, p in self.players.items():
                if p["name"] == name and not p["connected"]:
                    existing = s
                    break
            if existing:
                del self.players[existing]
            p = make_player_state()
            p["sid"] = sid
            p["name"] = name
            self.players[sid] = p
        self.add_notification(f"{chr(0x1F44B)} {name} 加入了游戏！")
        return p

    def remove_player(self, sid):
        with self.lock:
            if sid in self.players:
                p = self.players[sid]
                if self.world["phase"] == "lobby":
                    del self.players[sid]
                    self.add_notification(f"{chr(0x1F44B)} {p['name']} 离开了游戏")
                else:
                    p["connected"] = False
                    self.add_notification(f"⚠️ {p['name']} 断开连接")

    def reconnect_player(self, sid, name):
        with self.lock:
            for s, p in self.players.items():
                if p["name"] == name and not p["connected"]:
                    old_sid = s
                    p["sid"] = sid
                    p["connected"] = True
                    self.players[sid] = p
                    if old_sid != sid:
                        del self.players[old_sid]
                    return p
            return None

    # ---------- notifications ----------

    def add_notification(self, msg):
        ts = time.strftime("%H:%M:%S")
        self.notifications.append({"time": ts, "msg": msg})
        if len(self.notifications) > 200:
            self.notifications = self.notifications[-100:]
        try:
            socketio.emit("notification", {"time": ts, "msg": msg})
        except Exception:
            pass

    # ---------- fishing ----------

    def get_fishing_duration(self, sid):
        return FISHING_DURATION

    def get_clicks_per_fish(self, sid):
        p = self.players.get(sid)
        if not p:
            return 8
        base = BOAT_STATS[p["boat_level"]]["clicks_per_fish"]
        mult = 1.0
        ev = self.world.get("current_event") or {}
        if ev.get("effect") == "fishing_time_plus_50":
            mult *= 1.5
        if ev.get("effect") == "fishing_time_double_20_gold":
            mult *= 2.0
        pe = p.get("personal_event") or {}
        if pe.get("effect") == "fishing_time_plus_50_personal":
            mult *= 1.5
        return max(1, int(base * mult))

    def clicks_to_fish(self, sid, clicks):
        p = self.players.get(sid)
        if not p:
            return 0
        cpf = self.get_clicks_per_fish(sid)
        if cpf <= 0:
            return 0
        fish = clicks // cpf
        # Apply event/card multipliers
        mult = 1.0
        ev = self.world.get("current_event") or {}
        if ev.get("effect") == "catch_double":
            mult *= 2.0
        pe = p.get("personal_event") or {}
        if pe.get("effect") == "catch_half":
            mult *= 0.5
        if pe.get("effect") == "first_catch_bonus_50" and not p["first_fish_done"]:
            mult *= 1.5
        fish = int(fish * mult)
        # Depletion penalty
        if self.world["lake_fish"] < DEPLETION_THRESHOLD:
            fish = max(0, fish // 2)
        fish = min(fish, self.world["lake_fish"])
        return max(0, fish)

    def record_fishing_click(self, sid):
        with self.lock:
            p = self.players.get(sid)
            if not p or not p["is_fishing"] or p["eliminated"]:
                return {"clicks": 0, "fish": 0}
            p["fishing_clicks"] += 1
            fish = self.clicks_to_fish(sid, p["fishing_clicks"])
            return {"clicks": p["fishing_clicks"], "fish": fish}

    def start_fishing(self, sid):
        with self.lock:
            p = self.players.get(sid)
            if not p or p["eliminated"]:
                return {"error": "Player not found or eliminated"}
            if p["is_fishing"]:
                return {"error": "Already fishing"}
            if p["busy_until"] > 0 and self.world["time_remaining"] > p["busy_until"]:
                return {"error": "Busy with another action"}
            if self.world["phase"] != "playing":
                return {"error": "Not action time"}
            if self.world["lake_fish"] <= 0:
                return {"error": "Lake has no fish!"}
            trip_cost = BOAT_STATS[p["boat_level"]]["trip_cost"]
            if p["gold"] < trip_cost:
                return {"error": f"Need {trip_cost}g to go fishing!"}
            p["gold"] -= trip_cost
            duration = self.get_fishing_duration(sid)
            p["is_fishing"] = True
            p["fishing_start"] = self.world["time_remaining"]
            p["fishing_duration"] = duration
            p["fishing_end_at"] = self.world["time_remaining"] - duration
            p["fishing_clicks"] = 0
            boat = BOAT_STATS[p["boat_level"]]
            cpf = boat["clicks_per_fish"]
            self.add_notification(f"⛵ {p['name']} went fishing ({boat['name']}, {cpf} clicks/fish, -{trip_cost}g)")
            return {"ok": True, "duration": duration, "clicks_per_fish": cpf}

    def cancel_fishing(self, sid, forced=False, attacker_sid=None):
        with self.lock:
            p = self.players.get(sid)
            if not p or not p["is_fishing"]:
                return 0
            clicks = p.get("fishing_clicks", 0)
            actual_catch = self.clicks_to_fish(sid, clicks)
            p["fish"] += actual_catch
            self.world["lake_fish"] = max(0, self.world["lake_fish"] - actual_catch)
            p["daily_catch"] += actual_catch
            p["is_fishing"] = False
            p["fishing_start"] = 0
            p["fishing_end_at"] = 0
            p["fishing_clicks"] = 0
            p["first_fish_done"] = True
            if forced:
                self.add_notification(f"⏰ {p['name']}'s fishing ended early — caught {actual_catch} fish")
            elif attacker_sid:
                att = self.players.get(attacker_sid)
                att_name = att["name"] if att else "Unknown"
                self.add_notification(f"\U0001f4a5 {att_name} interrupted {p['name']}'s fishing! Caught {actual_catch} fish")
            else:
                self.add_notification(f"\U0001f519 {p['name']} returned early — caught {actual_catch} fish")
            return actual_catch

    def complete_fishing(self, sid):
        with self.lock:
            p = self.players.get(sid)
            if not p or not p["is_fishing"]:
                return
            clicks = p.get("fishing_clicks", 0)
            catch_amount = self.clicks_to_fish(sid, clicks)
            p["fish"] += catch_amount
            self.world["lake_fish"] = max(0, self.world["lake_fish"] - catch_amount)
            p["daily_catch"] += catch_amount
            p["is_fishing"] = False
            p["fishing_start"] = 0
            p["fishing_end_at"] = 0
            p["fishing_clicks"] = 0
            p["first_fish_done"] = True
            self.add_notification(f"✅ {p['name']} finished fishing — caught {catch_amount} fish!")
            socketio.emit("fishing_complete", {"catch": catch_amount, "total_fish": p["fish"]}, room=sid)
            socketio.emit("state_update", self.get_player_state(sid), room=sid)

    # ---------- attack ----------

    def get_attack_probability(self, attacker_sid, defender_sid):
        att = self.players.get(attacker_sid)
        dfd = self.players.get(defender_sid)
        if not att or not dfd:
            return 0
        att_power = BOAT_STATS[att["boat_level"]]["attack"]
        def_power = BOAT_STATS[dfd["boat_level"]]["defense"]
        pe = dfd.get("personal_event") or {}
        if pe.get("effect") == "defense_penalty_20":
            att_power += att_power * 0.2
        prob = att_power / (att_power + def_power)
        ev = self.world.get("current_event") or {}
        if ev.get("effect") == "attack_rate_half":
            prob *= 0.5
        return min(1.0, max(0.0, prob))

    def attack_player(self, attacker_sid, target_sid):
        with self.lock:
            att = self.players.get(attacker_sid)
            dfd = self.players.get(target_sid)
            if not att or att["eliminated"]:
                return {"error": "你不存在或已被淘汰"}
            if not dfd or dfd["eliminated"]:
                return {"error": "目标不存在或已被淘汰"}
            if att["busy_until"] > 0 and self.world["time_remaining"] > att["busy_until"]:
                return {"error": "你正在执行其他操作"}
            if self.world["time_remaining"] > att["attack_cooldown_until"] and att["attack_cooldown_until"] > 0:
                remaining = int(att["attack_cooldown_until"] - self.world["time_remaining"])
                return {"error": f"攻击冷却中，还需{max(0, remaining)}秒"}
            if att["is_fishing"]:
                return {"error": "捕鱼中无法攻击"}
            if att["sid"] == dfd["sid"]:
                return {"error": "不能攻击自己"}

            # Interrupt defender's fishing if active
            fishing_stolen = 0
            was_fishing = dfd["is_fishing"]
            if was_fishing:
                actual_catch = self.cancel_fishing(target_sid, attacker_sid=attacker_sid)
                fishing_stolen = int(actual_catch * 0.5)
                dfd["fish"] = max(0, dfd["fish"] - fishing_stolen)
                att["fish"] += fishing_stolen

            # Resolve attack
            prob = self.get_attack_probability(attacker_sid, target_sid)
            success = random.random() < prob

            result = {"success": success, "fishing_interrupted": was_fishing, "fishing_stolen": fishing_stolen}

            if success:
                if was_fishing:
                    stolen = fishing_stolen
                else:
                    stolen = int(dfd["fish"] * 0.3)
                    dfd["fish"] -= stolen
                    att["fish"] += stolen
                result["stolen_fish"] = stolen
                result["total_stolen"] = stolen
                self.add_notification(f"\U0001f4a5 {att['name']} 劫掠了 {dfd['name']}！抢走{stolen}条鱼")
            else:
                penalty = 15
                att["gold"] = max(0, att["gold"] - penalty)
                dfd["gold"] += penalty
                result["penalty"] = penalty
                self.add_notification(f"❌ {att['name']} 攻击 {dfd['name']} 失败，赔偿{penalty}金币")

            # Set cooldown (60 seconds from now)
            att["attack_cooldown_until"] = self.world["time_remaining"] - 60
            att["busy_until"] = self.world["time_remaining"] - 10

            socketio.emit("attacked", {
                "attacker": att["name"],
                "success": success,
                "stolen": result.get("total_stolen", 0),
                "fishing_interrupted": was_fishing,
                "my_fish": dfd["fish"],
                "my_gold": dfd["gold"],
            }, room=target_sid)

            return result

    # ---------- trade ----------

    def create_trade_order(self, sid, trade_type, fish_amount, gold_amount):
        with self.lock:
            p = self.players.get(sid)
            if not p or p["eliminated"]:
                return {"error": "玩家不存在或已被淘汰"}
            my_orders = [o for o in self.trade_orders if o["player_sid"] == sid]
            if len(my_orders) >= MAX_TRADE_ORDERS:
                return {"error": f"你已经有{MAX_TRADE_ORDERS}个挂单，请先取消一些"}
            if fish_amount <= 0 or gold_amount <= 0:
                return {"error": "数量必须大于0"}
            if trade_type == "sell" and p["fish"] < fish_amount:
                return {"error": "鱼库存不足"}
            if trade_type == "buy" and p["gold"] < gold_amount:
                return {"error": "金币不足"}
            order = {
                "id": str(uuid.uuid4())[:8],
                "player_sid": sid,
                "player_name": p["name"],
                "type": trade_type,
                "fish_amount": fish_amount,
                "gold_amount": gold_amount,
            }
            self.trade_orders.append(order)
            p["busy_until"] = self.world["time_remaining"] - 5
            self.add_notification(f"\U0001f91d {p['name']} 挂出交易：{trade_type=='sell' and '卖' or '买'}{fish_amount}鱼 / {gold_amount}金")
            return {"ok": True, "order": order}

    def accept_trade_order(self, sid, order_id):
        with self.lock:
            p = self.players.get(sid)
            if not p or p["eliminated"]:
                return {"error": "玩家不存在或已被淘汰"}
            order = None
            for o in self.trade_orders:
                if o["id"] == order_id:
                    order = o
                    break
            if not order:
                return {"error": "订单不存在"}
            if order["player_sid"] == sid:
                return {"error": "不能接受自己的订单"}
            creator = self.players.get(order["player_sid"])
            if not creator or creator["eliminated"]:
                self.trade_orders.remove(order)
                return {"error": "订单创建者不存在"}
            if order["type"] == "sell":
                if p["gold"] < order["gold_amount"]:
                    return {"error": "金币不足"}
                if creator["fish"] < order["fish_amount"]:
                    self.trade_orders.remove(order)
                    return {"error": "卖家鱼库存不足"}
                creator["fish"] -= order["fish_amount"]
                creator["gold"] += order["gold_amount"]
                p["fish"] += order["fish_amount"]
                p["gold"] -= order["gold_amount"]
            else:
                if p["fish"] < order["fish_amount"]:
                    return {"error": "鱼库存不足"}
                if creator["gold"] < order["gold_amount"]:
                    self.trade_orders.remove(order)
                    return {"error": "买家金币不足"}
                creator["gold"] -= order["gold_amount"]
                creator["fish"] += order["fish_amount"]
                p["gold"] += order["gold_amount"]
                p["fish"] -= order["fish_amount"]
            self.trade_orders.remove(order)
            self.add_notification(f"✅ {p['name']} 接受了 {creator['name']} 的交易")
            socketio.emit("trade_completed", {
                "order": order,
                "accepter": p["name"],
            }, room=order["player_sid"])
            return {"ok": True}

    def cancel_trade_order(self, sid, order_id):
        with self.lock:
            for o in list(self.trade_orders):
                if o["id"] == order_id and o["player_sid"] == sid:
                    self.trade_orders.remove(o)
                    return {"ok": True}
            return {"error": "订单不存在或不属于你"}

    # ---------- other actions ----------

    def eat_fish(self, sid):
        with self.lock:
            p = self.players.get(sid)
            if not p or p["eliminated"]:
                return {"error": "玩家不存在或已被淘汰"}
            if p["fish"] <= 0:
                return {"error": "没有鱼可吃"}
            if p["satiety"] >= MAX_SATIETY:
                return {"error": "饱腹度已满"}
            if p["eat_count_today"] >= MAX_EAT_PER_DAY:
                return {"error": f"今天已经吃了{MAX_EAT_PER_DAY}次，不能再吃了"}
            ev = self.world.get("current_event") or {}
            satiety_gain = 2 if ev.get("effect") == "eat_double" else 1
            p["fish"] -= 1
            p["satiety"] = min(MAX_SATIETY, p["satiety"] + satiety_gain)
            p["eat_count_today"] += 1
            p["busy_until"] = 0
            return {"ok": True, "satiety": p["satiety"], "fish": p["fish"]}

    def sell_to_system(self, sid, amount):
        with self.lock:
            p = self.players.get(sid)
            if not p or p["eliminated"]:
                return {"error": "玩家不存在或已被淘汰"}
            if amount is None or amount <= 0:
                amount = p["fish"]
            amount = min(amount, p["fish"])
            if amount <= 0:
                return {"error": "没有鱼可卖"}
            price = self.get_sell_price(sid)
            p["fish"] -= amount
            p["gold"] += amount * price
            return {"ok": True, "sold": amount, "price": price, "earned": amount * price, "fish": p["fish"], "gold": p["gold"]}

    def buy_from_system(self, sid, amount):
        with self.lock:
            p = self.players.get(sid)
            if not p or p["eliminated"]:
                return {"error": "玩家不存在或已被淘汰"}
            buy_price = self.get_buy_price()
            if buy_price is None:
                return {"error": "系统暂时不出售鱼"}
            cost = amount * buy_price
            if p["gold"] < cost:
                return {"error": "金币不足"}
            if amount > self.world["lake_fish"]:
                return {"error": "湖里没有这么多鱼"}
            p["gold"] -= cost
            p["fish"] += amount
            self.world["lake_fish"] -= amount
            return {"ok": True, "bought": amount, "price": buy_price, "cost": cost, "fish": p["fish"], "gold": p["gold"]}

    def get_sell_price(self, sid):
        p = self.players.get(sid)
        base = SELL_PRICE_BASE
        ev = self.world.get("current_event") or {}
        if ev.get("effect") == "price_change":
            base = 5
        return base

    def get_buy_price(self):
        ev = self.world.get("current_event") or {}
        if ev.get("effect") == "price_change":
            return 8
        return None

    def upgrade_boat(self, sid):
        with self.lock:
            p = self.players.get(sid)
            if not p or p["eliminated"]:
                return {"error": "玩家不存在或已被淘汰"}
            if p["is_fishing"]:
                return {"error": "捕鱼中无法升级"}
            if p["boat_level"] >= 4:
                return {"error": "已经是最高级别"}
            next_level = p["boat_level"] + 1
            cost = BOAT_STATS[next_level]["upgrade_cost"]
            if p["gold"] < cost:
                return {"error": f"金币不足，需要{cost}金币"}
            p["gold"] -= cost
            p["boat_level"] = next_level
            boat = BOAT_STATS[next_level]
            self.add_notification(f"⬆️ {p['name']} 升级到 {boat['name']}！")
            return {"ok": True, "boat_level": next_level, "boat": boat, "gold": p["gold"]}

    # ---------- events ----------

    def apply_personal_event(self, sid, event):
        p = self.players.get(sid)
        if not p:
            return
        if event["type"] == "instant":
            self._apply_instant_event(sid, event)
        # "today" and "consumable" types are stored for later use

    def _apply_instant_event(self, sid, event):
        p = self.players.get(sid)
        if not p:
            return
        effect = event["effect"]
        if effect == "gain_10_fish":
            p["fish"] += 10
        elif effect == "gain_20_gold":
            p["gold"] += 20
        elif effect == "pay_10_gold":
            p["gold"] -= 10
        elif effect == "satiety_minus_2":
            p["satiety"] = max(0, p["satiety"] - 2)
        elif effect == "free_repair":
            p["consumables"].append("free_repair")
    # ---------- day cycle ----------

    def start_day(self):
        print(f"[DAY] start_day called, current day={self.world['day']}", flush=True)
        with self.lock:
            self.world["day"] += 1
            print(f"[DAY] starting day {self.world['day']}", flush=True)
            self.world["time_remaining"] = DAY_DURATION
            self.world["phase"] = "playing"
            self.world["satiety_milestones"] = [100, 70, 40]

            # Draw world event
            self.world["current_event"] = random.choice(WORLD_EVENTS)
            ev = self.world["current_event"]

            # Apply world event immediate effects
            if ev["effect"] == "lake_fish_minus_100":
                self.world["lake_fish"] = max(0, self.world["lake_fish"] - 100)
                for p in self.players.values():
                    if not p["eliminated"]:
                        p["satiety"] = max(0, p["satiety"] - 1)
            elif ev["effect"] == "fishing_time_double_20_gold":
                for p in self.players.values():
                    if not p["eliminated"]:
                        p["gold"] += 20

            # Reset daily state
            for p in self.players.values():
                if not p["eliminated"]:
                    p["eat_count_today"] = 0
                    p["first_fish_done"] = False
                    p["is_fishing"] = False
                    p["fishing_start"] = 0
                    p["fishing_end_at"] = 0
                    p["busy_until"] = 0
                    p["busy_action"] = ""
                    p["daily_catch"] = 0
                    p["daily_modifiers"] = {}
                    p["personal_event"] = None

            # Clear trade orders
            self.trade_orders = []

            # Draw personal events (60% per player)
            for s, p in self.players.items():
                if not p["eliminated"] and p["connected"]:
                    if random.random() < 0.6:
                        all_events = POSITIVE_EVENTS + NEGATIVE_EVENTS
                        pe = random.choice(all_events)
                        p["personal_event"] = pe
                        self._apply_instant_event(s, pe)

            self.add_notification(f"\U0001f4c5 第{self.world['day']}天开始了！事件：{ev['icon']}{ev['name']} — {ev['desc']}")

            # Broadcast day start
            socketio.emit("day_start", {
                "day": self.world["day"],
                "time_remaining": self.world["time_remaining"],
                "event": ev,
                "lake_fish": self.world["lake_fish"],
            })
            print(f"[DAY] day {self.world['day']} started, day_start broadcast", flush=True)

    def end_day(self):
        print(f"[DAY] end_day called, day={self.world['day']}", flush=True)
        with self.lock:
            self.world["phase"] = "day_end"

            # 1. Force end all fishing
            for s in list(self.players.keys()):
                if self.players[s].get("is_fishing") and not self.players[s]["eliminated"]:
                    self.cancel_fishing(s, forced=True)

            # 2. Fish rot 10%
            for s, p in self.players.items():
                if p["eliminated"]:
                    continue
                if p["fish"] > 0:
                    rot_rate = FISH_ROT_RATE
                    pe = p.get("personal_event") or {}
                    if pe.get("effect") == "rot_30_percent":
                        rot_rate = 0.3
                    lost = int(p["fish"] * rot_rate)
                    p["fish"] -= lost

            # 5. Lake fish regrowth (logistic: fast when depleted, slow when abundant)
            lf = self.world["lake_fish"]
            growth = LAKE_GROWTH_RATE * lf * (1 - lf / LAKE_CARRYING_CAPACITY)
            self.world["lake_fish"] = max(100, int(lf + growth))

            # 6. Check eliminations
            eliminated_names = []
            for s, p in self.players.items():
                if not p["eliminated"] and p["satiety"] <= 0:
                    p["eliminated"] = True
                    eliminated_names.append(p["name"])
            for name in eliminated_names:
                self.add_notification(f"\U0001f480 {name} 因饱腹度归零被淘汰！")

            # 7. Eco-subsidy check
            ev = self.world.get("current_event") or {}
            if ev.get("effect") == "low_catch_bonus":
                for s, p in self.players.items():
                    if not p["eliminated"] and p.get("daily_catch", 0) < 10:
                        p["gold"] += 15
                        self.add_notification(f"\U0001f33f {p['name']} 因低捕鱼量获得15金币环保补贴！")

            # 9. Check game over
            active = [p for p in self.players.values() if not p["eliminated"] and p["connected"]]
            if self.world["day"] >= 30:
                self.world["phase"] = "game_over"
                self.add_notification("\U0001f3c6 游戏结束！经过30天的竞争...")
            elif len(active) == 0 and len(self.players) > 0:
                self.world["phase"] = "game_over"
                if active:
                    self.add_notification(f"\U0001f3c6 {active[0]['name']} 是最后的幸存者！")
                else:
                    self.add_notification("\U0001f480 所有玩家都被淘汰了！")

            # Broadcast day end
            socketio.emit("day_end", {
                "day": self.world["day"],
                "eliminated": eliminated_names,
                "phase": self.world["phase"],
                "players": self.get_teacher_player_list(),
                "lake_fish": self.world["lake_fish"],
            })

            # Schedule next day
            if self.world["phase"] == "day_end":
                self._pending_next_day = True

    def _tick_loop(self):
        while True:
            socketio.sleep(1)
            with self.lock:
                if self._pending_next_day:
                    self._pending_next_day = False
                    socketio.sleep(INTERLUDE_SECONDS)
                    if self.world["phase"] == "day_end":
                        self.start_day()
                    continue

                if self.world["phase"] != "playing":
                    continue

                # Decrease time
                self.world["time_remaining"] -= 1 * self.world["time_speed"]

                # Check fishing completions
                for s, p in list(self.players.items()):
                    if p.get("is_fishing") and not p["eliminated"]:
                        if self.world["time_remaining"] <= p["fishing_end_at"]:
                            self.complete_fishing(s)

                # Check busy expirations
                for s, p in list(self.players.items()):
                    if not p["eliminated"] and p["busy_until"] > 0:
                        if self.world["time_remaining"] <= p["busy_until"]:
                            p["busy_until"] = 0
                            p["busy_action"] = ""

                # Check satiety milestones
                ms = self.world.get("satiety_milestones", [])
                tr = int(self.world["time_remaining"])
                for threshold in list(ms):
                    if tr <= threshold:
                        ms.remove(threshold)
                        decay = 1
                        for s, p in self.players.items():
                            if not p["eliminated"]:
                                pe = p.get("personal_event") or {}
                                half_decay = pe.get("effect") == "satiety_decay_half"
                                actual = 0 if half_decay else decay
                                if actual > 0:
                                    p["satiety"] = max(0, p["satiety"] - actual)
                                    try: socketio.emit("state_update", self.get_player_state(s), room=s)
                                    except: pass
                        m, s = divmod(DAY_DURATION - threshold, 60)
                        self.add_notification(f"🍽️ Satiety -{decay} for all players ({m}:{s:02d} mark)")

                # Broadcast time
                socketio.emit("time_update", {
                    "time_remaining": max(0, int(self.world["time_remaining"])),
                    "day": self.world["day"],
                    "phase": self.world["phase"],
                    "lake_fish": self.world["lake_fish"],
                })

                # Check day end
                if self.world["time_remaining"] <= 0:
                    self.end_day()

    # ---------- state queries ----------

    def get_teacher_player_list(self):
        result = []
        for s, p in self.players.items():
            result.append({
                "name": p["name"],
                "satiety": p["satiety"],
                "fish": p["fish"],
                "gold": p["gold"],
                "boat_level": p["boat_level"],
                "boat_name": BOAT_STATS[p["boat_level"]]["name"],
                "is_fishing": p["is_fishing"],
                "attack_cooldown": max(0, int(p["attack_cooldown_until"] - self.world["time_remaining"])),
                "eliminated": p["eliminated"],
                "connected": p["connected"],                "personal_event_icon": p.get("personal_event", {}).get("icon", "") if p.get("personal_event") else "",
            })
        return result

    def get_teacher_state(self):
        ev = self.world.get("current_event") or {}
        connected = sum(1 for p in self.players.values() if p["connected"] and not p["eliminated"])
        offline = sum(1 for p in self.players.values() if not p["connected"] and not p["eliminated"])
        eliminated = sum(1 for p in self.players.values() if p["eliminated"])
        return {
            "day": self.world["day"],
            "time_remaining": max(0, int(self.world["time_remaining"])),
            "lake_fish": self.world["lake_fish"],
            "phase": self.world["phase"],
            "time_speed": self.world["time_speed"],
            "connected_count": connected,
            "offline_count": offline,
            "eliminated_count": eliminated,
            "current_event": {"icon": ev.get("icon", ""), "name": ev.get("name", ""), "desc": ev.get("desc", "")},
            "players": self.get_teacher_player_list(),
            "notifications": self.notifications[-30:],
        }

    def get_player_state(self, sid):
        p = self.players.get(sid)
        if not p:
            return None
        boat = BOAT_STATS[p["boat_level"]]
        pe = p.get("personal_event") or {}
        buy_price = self.get_buy_price()

        active_players = []
        for s, pl in self.players.items():
            if s != sid and not pl["eliminated"] and pl["connected"]:
                active_players.append({
                    "sid": s,
                    "name": pl["name"],
                    "boat_level": pl["boat_level"],
                    "boat_name": BOAT_STATS[pl["boat_level"]]["name"],
                    "is_fishing": pl["is_fishing"],
                })

        return {
            "me": {
                "name": p["name"],
                "satiety": p["satiety"],
                "fish": p["fish"],
                "gold": p["gold"],
                "boat_level": p["boat_level"],
                "boat_name": boat["name"],
                "clicks_per_fish": self.get_clicks_per_fish(sid),
                "trip_cost": boat["trip_cost"],
                "boat_attack": boat["attack"],
                "boat_defense": boat["defense"],
                "is_fishing": p["is_fishing"],
                "fishing_duration": p.get("fishing_duration", 30),
                "fishing_start": p.get("fishing_start", 0),
                "attack_cooldown": max(0, int(p["attack_cooldown_until"] - self.world["time_remaining"])),
                "busy_until": p["busy_until"],                "personal_event": pe,
                "eat_count_today": p["eat_count_today"],
                "eliminated": p["eliminated"],
                "consumables": p.get("consumables", []),
                "daily_catch": p.get("daily_catch", 0),
            },
            "world": {
                "day": self.world["day"],
                "time_remaining": max(0, int(self.world["time_remaining"])),
                "lake_fish": self.world["lake_fish"],
                "phase": self.world["phase"],
                "current_event": self.world["current_event"],
                "sell_price": self.get_sell_price(sid),
                "buy_price": buy_price,
            },
            "trade_orders": [o for o in self.trade_orders if o["player_sid"] != sid],
            "other_players": active_players,
        }

    def start_game(self):
        with self.lock:
            if self.world["phase"] != "lobby":
                return "游戏已经开始了"
            connected = [s for s, p in self.players.items() if p["connected"]]
            if len(connected) < 1:
                return "没有玩家加入，请等待玩家扫码加入"
            self.start_day()
            return None

    def reset_game(self):
        with self.lock:
            old_players = self.players
            self.__init__()
            for s, p in old_players.items():
                if p["connected"]:
                    np = make_player_state()
                    np["sid"] = s
                    np["name"] = p["name"]
                    self.players[s] = np
            self.add_notification("\U0001f504 游戏已重置")
            socketio.emit("game_reset", {})

    def skip_day(self):
        with self.lock:
            if self.world["phase"] == "playing":
                self.world["time_remaining"] = 1

    def set_speed(self, speed):
        with self.lock:
            self.world["time_speed"] = max(0.25, min(5.0, speed))

    def force_event(self, event_id):
        with self.lock:
            for ev in WORLD_EVENTS:
                if ev["id"] == event_id:
                    self.world["current_event"] = ev
                    self.add_notification(f"\U0001f4e2 教师触发了事件：{ev['icon']}{ev['name']}")
                    socketio.emit("day_start", {
                        "day": self.world["day"],
                        "time_remaining": max(0, int(self.world["time_remaining"])),
                        "event": ev,
                        "lake_fish": self.world["lake_fish"],
                    })
                    return True
            return False


# ============================================================
# FLASK APP & SOCKET.IO
# ============================================================

app = Flask(__name__)
app.config["SECRET_KEY"] = "tragedy-of-the-commons-secret"
socketio = SocketIO(app, async_mode="threading", cors_allowed_origins="*")
game = GameState()

# ============================================================
# HTTP ROUTES
# ============================================================

@app.after_request
def add_cors_headers(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "*"
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp

@app.route("/qr.png")
def qr_code():
    import qrcode, io
    join_url = f"http://{request.host}/join"
    img = qrcode.make(join_url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return Response(buf.getvalue(), mimetype="image/png")

@app.route("/")
def teacher_view():
    return Response(TEACHER_HTML, mimetype="text/html; charset=utf-8")

@app.route("/teacher")
def teacher_redirect():
    return Response(TEACHER_HTML, mimetype="text/html; charset=utf-8")

@app.route("/api/test")
def api_test():
    return jsonify({"ok": True, "msg": "API is working"})

@app.route("/api/start")
def api_start():
    try:
        err = game.start_game()
        if err: return jsonify({"error": err})
        print(f"[API] game started, day={game.world['day']}", flush=True)
        socketio.emit("teacher_update", game.get_teacher_state())
        return jsonify({"ok": True})
    except Exception as e:
        print(f"[API] start ERROR: {e}", flush=True)
        return jsonify({"error": str(e)})

@app.route("/api/reset")
def api_reset():
    try:
        game.reset_game()
        socketio.emit("teacher_update", game.get_teacher_state())
        return jsonify({"ok": True})
    except Exception as e:
        print(f"[API] reset ERROR: {e}", flush=True)
        return jsonify({"error": str(e)})

@app.route("/api/kick/<name>")
def api_kick(name):
    try:
        kicked = False
        for s, p in list(game.players.items()):
            if p["name"] == name:
                del game.players[s]
                kicked = True
                break
        if kicked:
            socketio.emit("teacher_update", game.get_teacher_state())
            return jsonify({"ok": True})
        return jsonify({"error": "Player not found"})
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/api/skip")
@app.route("/api/skip_day")
def api_skip():
    try:
        game.skip_day()
        return jsonify({"ok": True})
    except Exception as e:
        print(f"[API] skip ERROR: {e}", flush=True)
        return jsonify({"error": str(e)})

@app.route("/api/speed")
@app.route("/api/set_speed")
def api_speed():
    try:
        game.set_speed(float(request.args.get("v", "1")))
        socketio.emit("teacher_update", game.get_teacher_state())
        return jsonify({"ok": True})
    except Exception as e:
        print(f"[API] speed ERROR: {e}", flush=True)
        return jsonify({"error": str(e)})

@app.route("/ping")
def ping():
    return Response("pong", mimetype="text/plain")

@app.route("/join")
def player_view():
    return Response(PLAYER_HTML, mimetype="text/html; charset=utf-8")

@app.route("/socket.io.js")
def serve_socketio_js():
    js_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "socket.io.min.js")
    if os.path.exists(js_path):
        with open(js_path, "rb") as f:
            return Response(f.read(), mimetype="application/javascript")
    return Response("console.error('Socket.IO library not found. Place socket.io.min.js next to game.py');", mimetype="application/javascript")

# ============================================================
# SOCKET.IO EVENTS
# ============================================================

@socketio.on("connect")
def handle_connect():
    print(f"[CONNECT] {request.sid}", flush=True)
    emit("connected", {"sid": request.sid})
    emit("teacher_update", game.get_teacher_state())

@socketio.on("disconnect")
def handle_disconnect():
    print(f"[DISCONNECT] {request.sid}")
    game.remove_player(request.sid)

@socketio.on("join_game")
def handle_join(data):
    name = (data.get("name") or "").strip()[:20]
    print(f"[JOIN] sid={request.sid} name={name}")
    if not name:
        emit("error_msg", {"msg": "请输入昵称"})
        return
    reconnected = game.reconnect_player(request.sid, name)
    if reconnected:
        print(f"[JOIN] reconnected player {name}")
        emit("joined", game.get_player_state(request.sid))
        socketio.emit("teacher_update", game.get_teacher_state())
        return
    result = game.add_player(request.sid, name)
    if result is None:
        emit("error_msg", {"msg": "游戏已经开始，无法加入"})
        return
    print(f"[JOIN] new player {name}, total players: {len(game.players)}")
    emit("joined", game.get_player_state(request.sid))
    socketio.emit("teacher_update", game.get_teacher_state())
    # Notify all players to update waiting screen count
    active = [p["name"] for p in game.players.values() if p["connected"] and not p["eliminated"]]
    socketio.emit("lobby_update", {"count": len(active), "names": active})

@socketio.on("teacher_command")
def handle_teacher_command(data):
    cmd = data.get("command")
    print(f"[TEACHER] cmd={cmd} data={data}")
    if cmd == "start":
        err = game.start_game()
        print(f"[TEACHER] start result: {err}")
        if err:
            emit("error_msg", {"msg": err})
        else:
            socketio.emit("teacher_update", game.get_teacher_state())
            print(f"[TEACHER] game started, day={game.world['day']}")
    elif cmd == "reset":
        game.reset_game()
        socketio.emit("teacher_update", game.get_teacher_state())
    elif cmd == "skip_day":
        game.skip_day()
    elif cmd == "set_speed":
        game.set_speed(float(data.get("speed", 1.0)))
        socketio.emit("teacher_update", game.get_teacher_state())
    elif cmd == "force_event":
        game.force_event(data.get("event_id", ""))
    elif cmd == "get_state":
        emit("teacher_update", game.get_teacher_state())

@socketio.on("player_action")
def handle_player_action(data):
    sid = request.sid
    action = data.get("action")
    print(f"[ACTION] sid={sid} action={action} data={data}")
    result = {}

    if action == "start_fishing":
        result = game.start_fishing(sid)
    elif action == "fishing_click":
        result = game.record_fishing_click(sid)
    elif action == "cancel_fishing":
        game.cancel_fishing(sid)
        result = {"ok": True}
    elif action == "attack":
        target = data.get("target_sid")
        result = game.attack_player(sid, target)
    elif action == "create_trade":
        result = game.create_trade_order(sid, data.get("type"), int(data.get("fish", 0)), int(data.get("gold", 0)))
    elif action == "accept_trade":
        result = game.accept_trade_order(sid, data.get("order_id"))
    elif action == "cancel_trade":
        result = game.cancel_trade_order(sid, data.get("order_id"))
    elif action == "eat":
        result = game.eat_fish(sid)
    elif action == "sell_fish":
        result = game.sell_to_system(sid, data.get("amount"))
    elif action == "buy_fish":
        result = game.buy_from_system(sid, data.get("amount"))
    elif action == "upgrade_boat":
        result = game.upgrade_boat(sid)
    elif action == "get_state":
        state = game.get_player_state(sid)
        emit("state_update", state)
        return
    elif action == "rest":
        p = game.players.get(sid)
        if p and p["is_fishing"]:
            game.cancel_fishing(sid)
            result = {"ok": True, "msg": "已返航"}
        else:
            result = {"ok": True, "msg": "已在码头休息"}
    else:
        result = {"error": f"未知操作: {action}"}

    print(f"[ACTION] result: {result}")
    emit("action_result", result)

    # Update teacher after actions
    socketio.emit("teacher_update", game.get_teacher_state())

    # Send updated player state
    updated_state = game.get_player_state(sid)
    if updated_state:
        emit("state_update", updated_state)


# ============================================================
# HTML: TEACHER VIEW (Big Screen)
# ============================================================

TEACHER_HTML = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fish & Chips & Shipwreck - Teacher Screen</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#faf7f2;color:#1a1a1a;font-family:'Segoe UI',system-ui,-apple-system,sans-serif;height:100vh;overflow:hidden;display:flex;flex-direction:column}
.header{background:#ffffff;padding:14px 28px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #e8e5df}
.header h1{font-size:24px;font-weight:600;color:#1a1a1a;letter-spacing:-0.3px}
.clock-area{text-align:center}
.clock-digits{font-size:72px;font-weight:700;font-variant-numeric:tabular-nums;color:#1a1a1a;letter-spacing:-2px}
.clock-digits.warning{color:#c46849;animation:pulse 0.5s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.5}}
.clock-label{font-size:14px;color:#5e5d59;font-weight:500}
.big-stats{display:flex;gap:28px;align-items:center}
.big-stat{display:flex;flex-direction:column;align-items:center;min-width:90px}
.big-stat-icon{font-size:18px;margin-bottom:2px}
.big-stat-val{font-size:28px;font-weight:700;color:#1a1a1a;font-variant-numeric:tabular-nums}
.big-stat-lbl{font-size:10px;color:#5e5d59;text-transform:uppercase;letter-spacing:1.5px;font-weight:500}
.main{display:flex;flex:1;gap:20px;padding:20px;overflow:hidden}
.left-panel{flex:1;display:flex;flex-direction:column;gap:16px}
.panel-box{background:#ffffff;border-radius:14px;padding:18px;border:1px solid #e8e5df}
.panel-box h3{font-size:15px;font-weight:600;color:#1a1a1a;margin-bottom:12px}
.stat-row{display:flex;justify-content:space-between;padding:7px 0;border-bottom:1px solid #f0ede8}
.stat-row .label{color:#5e5d59;font-size:13px}
.stat-row .value{font-weight:600;font-size:14px;color:#1a1a1a}
.event-card{background:#ffffff;border-radius:14px;padding:20px;text-align:center;border:1px solid #e8e5df;transition:all 0.3s}
.event-card.big{padding:32px 24px}
.event-card .event-icon{font-size:40px;margin-bottom:4px;transition:font-size 0.3s}
.event-card.big .event-icon{font-size:64px}
.event-card .event-name{font-size:16px;font-weight:600;margin:6px 0;color:#1a1a1a;transition:font-size 0.3s}
.event-card.big .event-name{font-size:22px}
.event-card .event-desc{font-size:13px;color:#5e5d59;transition:font-size 0.3s}
.event-card.big .event-desc{font-size:16px}
.right-panel{flex:1;display:flex;flex-direction:column;gap:10px;overflow:hidden}
.player-table-wrap{flex:1;overflow-y:auto;border-radius:14px;border:1px solid #e8e5df;background:#ffffff}
.player-table{width:100%;border-collapse:collapse;font-size:13px}
.player-table th{background:#faf7f2;padding:10px 10px;text-align:left;position:sticky;top:0;z-index:1;font-weight:600;color:#5e5d59;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;border-bottom:1px solid #e8e5df}
.player-table td{padding:9px 10px;border-bottom:1px solid #f0ede8;color:#1a1a1a}
.player-table tr.fishing{background:rgba(196,104,73,0.08)}
.player-table tr.eliminated{background:rgba(196,104,73,0.08);opacity:0.55}
.player-table tr.disconnected{background:rgba(196,104,73,0.06);opacity:0.5}
.notification-bar{background:#ffffff;border-radius:14px;padding:10px 18px;overflow:hidden;white-space:nowrap;border:1px solid #e8e5df;min-height:44px}
.notification-scroll{display:inline-block;animation:scroll-left 30s linear infinite}
@keyframes scroll-left{0%{transform:translateX(100%)}100%{transform:translateX(-100%)}}
.notification-item{display:inline-block;margin-right:50px;font-size:13px;color:#5e5d59}
.controls{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.controls button{padding:7px 14px;border:1px solid #e8e5df;background:#ffffff;color:#1a1a1a;border-radius:8px;cursor:pointer;font-size:12px;font-weight:500;transition:all 0.15s}
.controls button:hover{background:#f0ede8;border-color:#c4bfb5}
.controls button.danger{background:#b43c3c;border-color:#b43c3c;color:#fff}
.controls button.danger:hover{background:#9a3030}
.controls button.success{background:#c46849;border-color:#c46849;color:#fff}
.controls button.success:hover{background:#b05a3d}
.speed-badge{padding:4px 10px;background:#ffffff;border-radius:6px;font-weight:600;color:#c46849;font-size:12px;border:1px solid #e8e5df}
.qr-area{text-align:center;padding:16px}
.qr-area p{font-size:13px;color:#5e5d59;margin:8px 0}
.qr-area .join-url{font-size:16px;color:#c46849;font-weight:600}
</style>
</head>
<body>
<div id="js-error" style="display:none;background:#b43c3c;color:#fff;padding:8px 16px;font-size:14px;text-align:center"><strong>JS Error:</strong> <span id="js-error-msg"></span></div>
<div id="gameover-banner" style="display:none;background:#c46849;color:#fff;padding:14px 24px;text-align:center;font-size:22px;font-weight:700">🏆 Game Over — <span id="go-winner"></span></div>
<div class="header">
<h1>🎣 Fish & Chips & Shipwreck</h1>
<div class="clock-area">
<div class="clock-digits" id="clock">2:00</div>
<div class="clock-label" id="day-label">Waiting to start...</div>
</div>
<div class="big-stats">
<div class="big-stat"><span class="big-stat-icon">📅</span><span class="big-stat-val" id="cur-day">0</span><span class="big-stat-lbl">Day</span></div>
<div class="big-stat"><span class="big-stat-icon">🐟</span><span class="big-stat-val" id="lake-fish">5000</span><span class="big-stat-lbl">Population</span></div>
</div>
<div class="controls" id="controls">
<button class="success" onclick="sendCmd('start')">▶ Start Game</button>
<button class="danger" onclick="sendCmd('reset')">↺ Reset</button>
<button onclick="sendCmd('skip_day')">⏭ Skip Day</button>
<button onclick="sendCmd('set_speed',{speed:0.5})">0.5×</button>
<button onclick="sendCmd('set_speed',{speed:1})">1×</button>
<button onclick="sendCmd('set_speed',{speed:2})">2×</button>
<span class="speed-badge" id="speed-badge">1×</span>
</div>
</div>
<div class="main">
<div class="left-panel">
<div class="event-card" id="event-card">
<div class="event-icon" id="event-icon">-</div>
<div class="event-name" id="event-name">Waiting</div>
<div class="event-desc" id="event-desc">The game hasn't started yet</div>
</div>
<div class="panel-box qr-area" id="qr-box">
<h3>📱 Join the Game</h3>
<p>Scan QR code or visit:</p>
<img src="/qr.png" id="qr-img" width="280" height="280" style="border-radius:12px;background:#fff;padding:6px;border:3px solid #c46849;transition:all 0.3s" alt="QR Code">
<p class="join-url" id="join-url"></p>
</div>
</div>
<div class="right-panel">
<div class="player-table-wrap">
<table class="player-table">
<thead><tr>
<th>#</th><th>Name</th><th>💰 Gold</th><th>🐟 Fish</th><th>⚓ Boat</th><th>Status</th><th></th>
</tr></thead>
<tbody id="player-tbody"></tbody>
</table>
</div>
<div class="notification-bar">
<div class="notification-scroll" id="notif-scroll"></div>
</div>
</div>
</div>
<script src="/socket.io.js"></script>
<script>
// === ERROR TRAP ===
window.onerror=function(m,s,l){document.getElementById('js-error').style.display='block';document.getElementById('js-error-msg').textContent=m+' (line '+l+')'};

// === COMMANDS (defined FIRST - always available) ===
function sendCmd(cmd,extra){
var url='/api/'+cmd;
if(extra&&extra.speed)url+='?v='+extra.speed;
fetch(url).then(function(r){return r.json()}).then(function(d){
if(d.error)alert(d.error);
}).catch(function(e){alert('Request failed: '+e.message)});
}

function kickPlayer(name){
if(!confirm('Kick '+name+'?'))return;
fetch('/api/kick/'+encodeURIComponent(name)).then(function(r){return r.json()}).then(function(d){
if(d.error)alert(d.error);
}).catch(function(e){alert('Kick failed: '+e.message)});
}


// === INIT ===
if(typeof io==='undefined'){document.body.innerHTML='<h2 style="color:red;padding:40px">Cannot load Socket.IO. Refresh.</h2>';throw new Error('io')}
var socket = io({transports:["polling","websocket"],timeout:30000,upgrade:false});
var joinUrl = window.location.origin + '/join';
var ju=document.getElementById('join-url'); if(ju)ju.textContent=joinUrl;

function F(s){var m=Math.floor(s/60),sec=Math.floor(s%60);return m+':'+(sec<10?'0':'')+sec}
function E(id){return document.getElementById(id)}


socket.on('connected',function(d){console.log('connected',d.sid)});
	socket.on('teacher_update',function(d){
	if(!d)return;
	var cc=d.connected_count||0, oc=d.offline_count||0;
	var label;
	if(d.phase==='lobby')label='Lobby - '+cc+' player'+(cc!==1?'s':'')+(oc>0?' | 🔴 '+oc+' offline':'');
	else if(d.phase==='day_end')label='Day '+d.day+' ended';
	else if(d.phase==='game_over'){label='Game Over';
		var gb=E('gameover-banner');if(gb){gb.style.display='block';
		var top=null;if(d.players)for(var i=0;i<d.players.length;i++){var p=d.players[i];if(!p.eliminated&&(top===null||p.gold>top.gold))top=p}
		var gw=E('go-winner');if(gw&&top)gw.textContent=top.name+' wins with '+top.gold+' gold!'}}
	else label='Day '+d.day+' | '+cc+' online'+(oc>0?' 🔴'+oc:'');
	var dl=E('day-label');if(dl)dl.textContent=label;
	var cd=E('cur-day');if(cd)cd.textContent=d.day;
	var lf=E('lake-fish');if(lf)lf.textContent=d.lake_fish;
	var cl=E('clock');if(cl){cl.textContent=F(d.time_remaining);cl.classList.toggle('warning',d.time_remaining<=30)}
	var sb=E('speed-badge');if(sb)sb.textContent=d.time_speed+'×';
	if(d.current_event&&d.current_event.icon){
		var ei=E('event-icon');if(ei)ei.textContent=d.current_event.icon;
		var en=E('event-name');if(en)en.textContent=d.current_event.name;
		var ed=E('event-desc');if(ed)ed.textContent=d.current_event.desc;}
			var ec=E('event-card');if(ec&&d.current_event.color)ec.style.background=d.current_event.color;
	var tb=E('player-tbody');if(tb){
		var players=d.players||[];
		players.sort(function(a,b){return b.gold-a.gold});
		tb.innerHTML=players.map(function(p,i){
			var cls=[];
			if(p.eliminated)cls.push('eliminated');
			else if(!p.connected)cls.push('disconnected');
			else if(p.is_fishing)cls.push('fishing');
			var rank=i+1;var crown=(i===0&&!p.eliminated&&d.phase==='playing')?'👑 ':'';
			var s='';
			if(p.eliminated)s='💀 Eliminated';
			else if(!p.connected)s='🔴 OFFLINE';
			else if(p.is_fishing)s='🎣 Fishing';
			else if(p.attack_cooldown>0)s='⏳ CD '+p.attack_cooldown+'s';
			else s='🟢 Idle';
			return '<tr class="'+cls.join(' ')+'"><td>'+crown+rank+'</td><td><strong>'+p.name+'</strong></td><td>'+p.gold+'</td><td>'+p.fish+'</td><td>'+p.boat_name+'</td><td>'+s+'</td><td style="cursor:pointer;color:#b43c3c;font-weight:700" onclick="kickPlayer(\''+p.name+'\')">✕</td></tr>';
		}).join('');
	}
	var ns=E('notif-scroll');if(ns){
		var notifs=d.notifications||[];
		ns.innerHTML=notifs.map(function(n){return '<span class="notification-item">['+n.time+'] '+n.msg+'</span>';}).join(' &nbsp;|&nbsp; ');
	}
	});

socket.on('time_update',function(d){
var cl=E('clock'); if(cl){cl.textContent=F(d.time_remaining);cl.classList.toggle('warning',d.time_remaining<=30)}
var cd=E('cur-day'); if(cd)cd.textContent=d.day;
var lf=E('lake-fish'); if(lf)lf.textContent=d.lake_fish;
});

socket.on('day_start',function(d){
var dl=E('day-label'); if(dl)dl.textContent='Day '+d.day;
var cd=E('cur-day'); if(cd)cd.textContent=d.day;
var lf=E('lake-fish'); if(lf)lf.textContent=d.lake_fish;
if(d.event){
var ei=E('event-icon'); if(ei)ei.textContent=d.event.icon;
var en=E('event-name'); if(en)en.textContent=d.event.name;
var ed=E('event-desc'); if(ed)ed.textContent=d.event.desc;
	var ec=E('event-card');if(ec&&d.event.color)ec.style.background=d.event.color;
}
var cl=E('clock'); if(cl)cl.classList.remove('warning');
	var qr=E("qr-img");if(qr){qr.width=120;qr.height=120};var ec=E("event-card");if(ec)ec.classList.add("big")
});


socket.on('error_msg',function(data){alert(data.msg)});
		socket.on("game_reset",function(){["day-label","clock","event-icon","event-name","event-desc","player-tbody","notif-scroll","cur-day","lake-fish"].forEach(function(id){var el=document.getElementById(id);if(!el)return;if(id==="day-label")el.textContent="Lobby";if(id==="clock"){el.textContent="2:00";el.classList.remove("warning")}if(id==="event-icon")el.textContent="-";if(id==="event-name")el.textContent="Waiting";if(id==="event-desc")el.textContent="Game has been reset";if(id==="player-tbody"||id==="notif-scroll")el.innerHTML="";if(id==="cur-day")el.textContent="0";if(id==="lake-fish")el.textContent="5000"})});
	var qr=E("qr-img");if(qr){qr.width=280;qr.height=280};var ec=E("event-card");if(ec)ec.classList.remove("big")

</script>
</body>
</html>'''

# ============================================================
# HTML: PLAYER VIEW (Mobile)
# ============================================================

PLAYER_HTML = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no">
<title>Fish & Chips & Shipwreck</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#faf7f2;color:#1a1a1a;font-family:'Segoe UI',system-ui,-apple-system,sans-serif;height:100vh;overflow:hidden;display:flex;flex-direction:column;user-select:none;-webkit-user-select:none}
body.warning{border:4px solid #c46849;animation:border-flash 0.5s infinite}
@keyframes border-flash{0%,100%{border-color:#c46849}50%{border-color:transparent}}

#join-screen{display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;gap:20px;padding:20px;background:#faf7f2}
#join-screen h2{font-size:24px;color:#1a1a1a;font-weight:600}
#join-screen input{padding:14px;font-size:18px;border-radius:10px;border:1px solid #e8e5df;background:#ffffff;color:#1a1a1a;width:260px;text-align:center;outline:none}
#join-screen input:focus{border-color:#c46849}
#join-screen button{padding:14px 40px;font-size:18px;border-radius:10px;background:#c46849;color:#faf7f2;border:none;cursor:pointer;font-weight:600;transition:background 0.15s}
#join-screen button:hover{background:#e0c8b8}

#game-screen{display:none;flex-direction:column;height:100%;padding:10px;background:#faf7f2}
#game-screen.active{display:flex}

.top-bar{display:flex;justify-content:space-between;align-items:center;padding:8px 4px}
.clock-ring{position:relative;width:70px;height:70px}
.clock-ring svg{transform:rotate(-90deg)}
.clock-ring .bg{fill:none;stroke:#e8e5df;stroke-width:6}
.clock-ring .fg{fill:none;stroke:#c46849;stroke-width:6;stroke-linecap:round;transition:stroke-dashoffset 1s linear}
.clock-ring .fg.warning{stroke:#c46849}
.clock-ring .time-text{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);font-size:15px;font-weight:700;color:#1a1a1a}
.day-badge{background:#ffffff;padding:8px 14px;border-radius:20px;font-weight:600;font-size:13px;color:#1a1a1a;border:1px solid #e8e5df}

.resources{display:flex;gap:8px;margin:8px 0}
.res-card{flex:1;background:#ffffff;border-radius:12px;padding:10px;text-align:center;border:1px solid #e8e5df}
.res-card .icon{font-size:18px}
.res-card .val{font-size:18px;font-weight:700;margin-top:2px;color:#1a1a1a}
.res-card .lbl{font-size:10px;color:#5e5d59;font-weight:500}

.info-banner{background:#ffffff;border-radius:10px;padding:8px 14px;margin:4px 0;font-size:12px;text-align:center;border:1px solid #e8e5df;color:#5e5d59}

.action-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:8px 0}
.action-btn{background:#ffffff;border:1px solid #e8e5df;border-radius:12px;padding:12px 6px;text-align:center;cursor:pointer;transition:all 0.15s;font-size:11px;color:#5e5d59}
.action-btn:active{background:#f0ede8;transform:scale(0.95)}
.action-btn .btn-icon{font-size:26px;display:block;margin-bottom:3px}
.action-btn.disabled{opacity:0.3;pointer-events:none}
.action-btn.highlight{border-color:#c46849}

#fishing-overlay{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:#faf7f2;z-index:100;flex-direction:column;align-items:center;justify-content:center;gap:8px}
#fishing-overlay.active{display:flex}
#fishing-overlay.shake{animation:shake 0.5s ease-in-out}
@keyframes shake{0%,100%{transform:translateX(0)}25%{transform:translateX(-10px)}75%{transform:translateX(10px)}}
#fish-tap-area:active{transform:scale(0.92)}
.tap-ripple{position:absolute;border-radius:50%;background:rgba(196,104,73,0.4);animation:ripple 0.6s ease-out;pointer-events:none}
@keyframes ripple{0%{width:0;height:0;opacity:1}100%{width:200px;height:200px;opacity:0}}
#waiting-overlay{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:#faf7f2;z-index:90;flex-direction:column;align-items:center;justify-content:center}
#waiting-overlay.active{display:flex}
@keyframes spin{0%{transform:rotate(0deg)}100%{transform:rotate(360deg)}}

.modal{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.8);z-index:200;flex-direction:column}
.modal.active{display:flex}
.modal-content{background:#ffffff;margin:40px 16px;border-radius:16px;padding:20px;flex:1;overflow-y:auto;border:1px solid #e8e5df}
.modal-content h3{font-size:18px;margin-bottom:12px;color:#1a1a1a;font-weight:600}
.modal-close{float:right;background:none;border:none;color:#5e5d59;font-size:24px;cursor:pointer}
.player-target{display:flex;justify-content:space-between;align-items:center;padding:12px;background:#faf7f2;border-radius:10px;margin:6px 0;cursor:pointer;border:1px solid #e8e5df}
.player-target:active{background:#f0ede8}
.player-target .pname{font-weight:600;color:#1a1a1a}
.player-target .pinfo{font-size:12px;color:#5e5d59}
.trade-order{background:#faf7f2;border-radius:10px;padding:12px;margin:6px 0;border:1px solid #e8e5df}
.trade-order .tinfo{display:flex;justify-content:space-between;align-items:center}
.trade-order button{padding:6px 14px;border-radius:8px;background:#c46849;color:#faf7f2;border:none;cursor:pointer;font-size:13px;font-weight:600}
.my-orders{margin-bottom:16px}
input[type=number]{padding:10px;border-radius:8px;border:1px solid #e8e5df;background:#faf7f2;color:#1a1a1a;width:80px;text-align:center;font-size:16px}
select{padding:10px;border-radius:8px;border:1px solid #e8e5df;background:#faf7f2;color:#1a1a1a}
</style>
</head>
<body>

<!-- Join Screen -->
<div id="join-screen">
<h2>🎣 Fish & Chips & Shipwreck</h2>
<input type="text" id="name-input" placeholder="Enter your nickname" maxlength="20" autocomplete="off">
<button onclick="joinGame()">Join Game</button>
<p style="color:#5e5d59;font-size:12px" id="join-error"></p>
</div>

<!-- Waiting Overlay (Kahoot-style lobby) -->
<div id="waiting-overlay">
<div style="text-align:center">
<div style="font-size:56px;margin-bottom:16px">🎣</div>
<h2 style="color:#1a1a1a;font-size:22px;margin-bottom:8px;font-weight:600">Waiting for the game to start...</h2>
<p style="color:#5e5d59;font-size:14px;margin-bottom:4px">The host will begin soon</p>
<p style="color:#c46849;font-size:36px;font-weight:700;margin:16px 0" id="waiting-player-count">1</p>
<p style="color:#5e5d59;font-size:13px">players joined</p>
<div style="margin-top:24px">
<div style="width:40px;height:40px;border:4px solid #e8e5df;border-top-color:#c46849;border-radius:50%;animation:spin 1s linear infinite;margin:0 auto"></div>
</div>
</div>
</div>

<!-- Sleeping Overlay (between days) -->
<div id="sleeping-overlay" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:#faf7f2;z-index:95;flex-direction:column;align-items:center;justify-content:center">
<div style="text-align:center">
<div style="font-size:48px;margin-bottom:16px">😴</div>
<h2 style="color:#1a1a1a;font-size:22px;margin-bottom:8px;font-weight:600">Sleeping...</h2>
<p style="color:#5e5d59;font-size:14px">Waiting for the next day</p>
</div>
</div>

<!-- Game Over Overlay (player) -->
<div id="gameover-overlay" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:#faf7f2;z-index:95;flex-direction:column;align-items:center;justify-content:center">
<div style="text-align:center">
<div style="font-size:56px;margin-bottom:16px">🏆</div>
<h2 style="color:#1a1a1a;font-size:24px;margin-bottom:8px;font-weight:600">Game Over</h2>
<p style="color:#5e5d59;font-size:14px" id="go-player-msg">Thanks for playing!</p>
</div>
</div>

<!-- Game Screen -->
<div id="game-screen">
<div class="top-bar">
<div class="clock-ring">
<svg width="70" height="70" viewBox="0 0 70 70">
<circle class="bg" cx="35" cy="35" r="30"/>
<circle class="fg" id="clock-circle" cx="35" cy="35" r="30" stroke-dasharray="188.5" stroke-dashoffset="0"/>
</svg>
<div class="time-text" id="time-text">2:00</div>
</div>
<div class="day-badge" id="day-badge">Day 0</div>
</div>

<div class="resources">
<div class="res-card"><div class="icon">🥩</div><div class="val" id="r-satiety">5</div><div class="lbl">Satiety</div></div>
<div class="res-card"><div class="icon">🐟</div><div class="val" id="r-fish">5</div><div class="lbl">Fish</div></div>
<div class="res-card"><div class="icon">💰</div><div class="val" id="r-gold">20</div><div class="lbl">Gold</div></div>
<div class="res-card"><div class="icon">⚓</div><div class="val" id="r-boat">Lv1</div><div class="lbl">Boat</div></div>
</div>

<div class="info-banner" id="info-banner" style="display:none"></div>

<div class="action-grid" id="action-grid">
<button class="action-btn" id="btn-fish" onclick="startFishing()"><span class="btn-icon">⚓</span>Fish</button>
<button class="action-btn" id="btn-attack" onclick="openAttackModal()"><span class="btn-icon">⚔️</span>Attack</button>
<button class="action-btn" id="btn-trade" onclick="openTradeModal()"><span class="btn-icon">🤝</span>Trade</button>
<button class="action-btn" id="btn-eat" onclick="eatFish()"><span class="btn-icon">🍽️</span>Eat</button>
<button class="action-btn" id="btn-sell" onclick="sellFish()"><span class="btn-icon">💰</span>Sell</button>
<button class="action-btn" id="btn-rest" onclick="doRest()"><span class="btn-icon">🏠</span>Rest</button>
<button class="action-btn" id="btn-upgrade" onclick="upgradeBoat()"><span class="btn-icon">⬆️</span>Upgrade</button>

</div>
<div id="status-bar" style="background:#ffffff;border-radius:10px;padding:8px 12px;margin:4px 0;font-size:12px;text-align:center;color:#5e5d59;min-height:20px;border:1px solid #e8e5df">Ready</div>
</div>

<!-- Fishing Overlay -->
<div id="fishing-overlay">
<div style="font-size:14px;color:#5e5d59;margin-bottom:4px" id="fish-time-left">20s</div>
<div style="width:80%;height:6px;background:#e8e5df;border-radius:3px;margin-bottom:12px">
<div id="fish-progress-bar" style="width:100%;height:100%;background:#c46849;border-radius:3px;transition:width 0.5s linear"></div>
</div>
<div id="fish-tap-area" style="width:180px;height:180px;border-radius:50%;background:radial-gradient(circle,#1f1a10,#ffffff);display:flex;align-items:center;justify-content:center;cursor:pointer;user-select:none;-webkit-user-select:none;-webkit-tap-highlight-color:transparent;border:3px solid #c46849;position:relative;overflow:hidden">
<div style="text-align:center;pointer-events:none">
<div style="font-size:48px;font-weight:700;color:#c46849" id="fish-catch-now">0</div>
<div style="font-size:13px;color:#c46849">fish caught</div>
</div>
</div>
<div style="margin-top:12px;font-size:13px;color:#5e5d59" id="fish-click-info">8 clicks = 1 fish</div>
<button onclick="cancelFishing()" style="margin-top:16px;padding:12px 36px;font-size:15px;border-radius:10px;background:#ffffff;color:#1a1a1a;border:1px solid #e8e5df;cursor:pointer;font-weight:600">🔙 Return to Port</button>
</div>

<!-- Attack Modal -->
<div class="modal" id="attack-modal">
<div class="modal-content">
<button class="modal-close" onclick="closeModal('attack-modal')">&times;</button>
<h3>⚔️ Choose Target</h3>
<div id="attack-targets"><p style="color:#5e5d59">Loading...</p></div>
</div>
</div>

<!-- Trade Modal -->
<div class="modal" id="trade-modal">
<div class="modal-content">
<button class="modal-close" onclick="closeModal('trade-modal')">&times;</button>
<h3>🤝 Trade Market</h3>
<div class="my-orders">
<h4 style="font-size:14px;color:#5e5d59;margin-bottom:6px">Create Order</h4>
<select id="trade-type" style="padding:10px;border-radius:8px;border:1px solid #e8e5df;background:#faf7f2;color:#1a1a1a;margin-bottom:8px;width:100%">
<option value="sell">Sell Fish for Gold</option>
<option value="buy">Buy Fish with Gold</option>
</select>
<div style="display:flex;align-items:center;justify-content:center;gap:4px;margin:6px 0">
<button onclick="adjTrade('fish',-5)" style="padding:8px 12px;border-radius:6px;background:#e8e5df;color:#1a1a1a;border:none;font-weight:700;font-size:14px">-5</button>
<button onclick="adjTrade('fish',-1)" style="padding:8px 12px;border-radius:6px;background:#e8e5df;color:#1a1a1a;border:none;font-weight:700;font-size:14px">-1</button>
<span style="font-size:20px;font-weight:700;min-width:40px;text-align:center" id="trade-fish-val">1</span><span>🐟</span>
<button onclick="adjTrade('fish',1)" style="padding:8px 12px;border-radius:6px;background:#c46849;color:#fff;border:none;font-weight:700;font-size:14px">+1</button>
<button onclick="adjTrade('fish',5)" style="padding:8px 12px;border-radius:6px;background:#c46849;color:#fff;border:none;font-weight:700;font-size:14px">+5</button>
</div>
<div style="display:flex;align-items:center;justify-content:center;gap:4px;margin:6px 0">
<button onclick="adjTrade('gold',-5)" style="padding:8px 12px;border-radius:6px;background:#e8e5df;color:#1a1a1a;border:none;font-weight:700;font-size:14px">-5</button>
<button onclick="adjTrade('gold',-1)" style="padding:8px 12px;border-radius:6px;background:#e8e5df;color:#1a1a1a;border:none;font-weight:700;font-size:14px">-1</button>
<span style="font-size:20px;font-weight:700;min-width:40px;text-align:center" id="trade-gold-val">1</span><span>💰</span>
<button onclick="adjTrade('gold',1)" style="padding:8px 12px;border-radius:6px;background:#c46849;color:#fff;border:none;font-weight:700;font-size:14px">+1</button>
<button onclick="adjTrade('gold',5)" style="padding:8px 12px;border-radius:6px;background:#c46849;color:#fff;border:none;font-weight:700;font-size:14px">+5</button>
</div>
<button onclick="createTrade()" style="padding:10px 20px;border-radius:8px;background:#c46849;color:#fff;border:none;cursor:pointer;font-weight:700;width:100%;margin-top:8px">Post Order</button>
</div>
<h4 style="font-size:14px;color:#5e5d59;margin:12px 0 6px">Active Orders</h4>
<div id="trade-orders"><p style="color:#5e5d59">No active orders</p></div>
</div>
</div>

<!-- Sell to System Modal -->
<div class="modal" id="sell-modal">
<div class="modal-content">
<button class="modal-close" onclick="closeModal('sell-modal')">&times;</button>
<h3>💰 Sell Fish to System</h3>
<p style="color:#5e5d59;margin:8px 0" id="sell-price-info">Price: 2 gold/fish</p>
<div style="display:flex;align-items:center;justify-content:center;gap:4px;margin:12px 0">
<button onclick="adjSell(-5)" style="padding:10px 16px;border-radius:8px;background:#e8e5df;color:#1a1a1a;border:none;font-weight:700;font-size:16px">-5</button>
<button onclick="adjSell(-1)" style="padding:10px 16px;border-radius:8px;background:#e8e5df;color:#1a1a1a;border:none;font-weight:700;font-size:16px">-1</button>
<span style="font-size:28px;font-weight:900;min-width:50px;text-align:center" id="sell-amount-val">1</span><span>🐟</span>
<button onclick="adjSell(1)" style="padding:10px 16px;border-radius:8px;background:#c46849;color:#fff;border:none;font-weight:700;font-size:16px">+1</button>
<button onclick="adjSell(5)" style="padding:10px 16px;border-radius:8px;background:#c46849;color:#fff;border:none;font-weight:700;font-size:16px">+5</button>
<button onclick="adjSell(999)" style="padding:10px 12px;border-radius:8px;background:#fff;color:#1a1a1a;border:1px solid #e8e5df;font-weight:700;font-size:14px">ALL</button>
</div>
<p style="text-align:center;color:#c46849;font-weight:700" id="sell-total-info">Total: 2 gold</p>
<button onclick="confirmSell()" style="padding:12px 20px;border-radius:8px;background:#c46849;color:#fff;border:none;cursor:pointer;font-weight:700;width:100%;font-size:16px">Confirm Sell</button>
</div>
</div>

<!-- Result toast -->
<div id="toast" style="display:none;position:fixed;top:20px;left:50%;transform:translateX(-50%);background:#c46849;color:#1a1a1a;padding:12px 24px;border-radius:24px;z-index:300;font-weight:700;font-size:15px;text-align:center"></div>

<script src="/socket.io.js"></script>
<script>
if(typeof io==='undefined'){document.body.innerHTML='<div style="color:red;padding:40px;font-size:18px;text-align:center"><h2>Network Error</h2><p>Cannot load Socket.IO. Check internet and refresh.</p></div>';throw new Error('io not loaded')}
var socket = io({transports:["polling","websocket"],timeout:30000,upgrade:false});
var myState = null;
var mySid = null;
var fishingTimer = null;
var globalTimeRemaining = 300;
var fishingStartGlobal = 0;
var fishingDuration = 20;
var fishingClicksPerFish = 8;
var sock_clicks = 0;
var isFishing = false;

function setStatus(msg,isError){
var s=document.getElementById('status-bar');
if(s){s.textContent=msg;s.style.color=isError?'#c46849':'#5e5d59'}
}
function showToast(msg,duration,isError){
var t=document.getElementById('toast');
t.textContent=msg;t.style.display='block';
t.style.background=isError?'#b43c3c':'#c46849';
setTimeout(function(){t.style.display='none'},duration||2000);
setStatus(msg,isError);
}

function formatTime(s){var m=Math.floor(s/60);var sec=Math.floor(s%60);return m+':'+(sec<10?'0':'')+sec}

function updateClock(){
var circle=document.getElementById('clock-circle');
var circumference=188.5;
var remaining=Math.max(0,globalTimeRemaining);
var offset=circumference*(1-remaining/120);
circle.setAttribute('stroke-dashoffset',offset);
document.getElementById('time-text').textContent=formatTime(remaining);
if(remaining<=30){circle.classList.add('warning');document.body.classList.add('warning')}
else{circle.classList.remove('warning');document.body.classList.remove('warning')}
}

function updateFishingProgress(){
if(!isFishing)return;
var elapsed=fishingStartGlobal-globalTimeRemaining;
var remaining=Math.max(0,fishingDuration-elapsed);
var pct=Math.max(0,Math.min(100,100*remaining/fishingDuration));
var bar=document.getElementById('fish-progress-bar');
if(bar)bar.style.width=pct+'%';
var tl=document.getElementById('fish-time-left');
if(tl)tl.textContent=Math.ceil(remaining)+'s remaining';
if(remaining<=0&&isFishing){
isFishing=false;
document.getElementById('fishing-overlay').classList.remove('active');
clearInterval(fishingTimer);
}
}

function handleFishingTap(e){
if(!isFishing)return;
sock_clicks++;
document.getElementById('fish-catch-now').textContent=Math.floor(sock_clicks/fishingClicksPerFish);
// Ripple effect
var ripple=document.createElement('div');
ripple.className='tap-ripple';
var area=document.getElementById('fish-tap-area');
var rect=area.getBoundingClientRect();
var x=e.clientX-rect.left-15,y=e.clientY-rect.top-15;
ripple.style.left=x+'px';ripple.style.top=y+'px';
ripple.style.width='30px';ripple.style.height='30px';
area.appendChild(ripple);
setTimeout(function(){ripple.remove()},600);
// Send click to server
socket.emit('player_action',{action:'fishing_click'});
}

function joinGame(){
var name=document.getElementById('name-input').value.trim();
if(!name){document.getElementById('join-error').textContent='Please enter a nickname';return}
socket.emit('join_game',{name:name});
}

socket.on('error_msg',function(d){
document.getElementById('join-error').textContent=d.msg;
showToast(d.msg,3000);
});

socket.on('joined',function(d){
document.getElementById('join-screen').style.display='none';
var isPlaying=(d.world||{}).phase==='playing';
if(!isPlaying){
document.getElementById('waiting-overlay').classList.add('active');
document.getElementById('game-screen').classList.add('active');
applyState(d);
}else{
document.getElementById('game-screen').classList.add('active');
applyState(d);
}
});

socket.on('lobby_update',function(d){
document.getElementById('waiting-player-count').textContent=d.count||1;
});

socket.on('state_update',function(d){
applyState(d);
var pc=d.other_players?d.other_players.length+1:1;
document.getElementById('waiting-player-count').textContent=pc;
});

socket.on('action_result',function(d){
if(d.error){showToast(d.error,4000,true);return}
if(d.msg){showToast(d.msg,2500)}
else if(d.ok){setStatus('OK',false)}
});

function refreshButtons(){
if(!myState||!myState.me)return;
var m=myState.me;
var busy=m.busy_until>0&&globalTimeRemaining>m.busy_until;
var cooldown=m.attack_cooldown>0;
var btns={
'btn-fish':m.is_fishing||busy||m.eliminated,
'btn-attack':busy||m.is_fishing||cooldown||m.eliminated,
'btn-trade':busy||m.is_fishing||m.eliminated,
'btn-eat':m.fish<=0||m.satiety>=5||m.eat_count_today>=5||busy||m.eliminated,
'btn-sell':m.fish<=0||busy||m.eliminated,
'btn-rest':m.eliminated,
'btn-upgrade':m.boat_level>=4||m.is_fishing||busy||m.eliminated,
};
for(var id in btns){
var el=document.getElementById(id);if(el)el.classList.toggle('disabled',btns[id]);
}
}

socket.on('time_update',function(d){
globalTimeRemaining=d.time_remaining;
updateClock();
if(isFishing)updateFishingProgress();
refreshButtons();
});

socket.on('day_start',function(d){
document.getElementById('waiting-overlay').classList.remove('active');
document.getElementById('sleeping-overlay').style.display='none';
document.getElementById('gameover-overlay').style.display='none';
	document.getElementById('info-banner').style.display='none';
document.getElementById('game-screen').style.display='';
globalTimeRemaining=d.time_remaining;
updateClock();
isFishing=false;
document.getElementById('fishing-overlay').classList.remove('active');
clearInterval(fishingTimer);
document.getElementById('day-badge').textContent='Day '+d.day;
});

socket.on('day_end',function(d){
isFishing=false;
document.getElementById('fishing-overlay').classList.remove('active');
clearInterval(fishingTimer);
if(d.phase==='game_over'){
document.getElementById('gameover-overlay').style.display='flex';
document.getElementById('game-screen').style.display='none';
}else{
document.getElementById('sleeping-overlay').style.display='flex';
}
if(d.eliminated&&d.eliminated.length>0){
showToast('Players eliminated: '+d.eliminated.join(', '),4000);
}
});

socket.on('fishing_complete',function(d){
showToast('🎉 Caught '+d.catch+' fish! Total: '+d.total_fish,3000);
if(navigator.vibrate)navigator.vibrate([100,50,100]);
	isFishing=false;sock_clicks=0;
});

socket.on('attacked',function(d){
isFishing=false;
	sock_clicks=0;
document.getElementById('fishing-overlay').classList.remove('active');
document.getElementById('fishing-overlay').classList.add('shake');
setTimeout(function(){document.getElementById('fishing-overlay').classList.remove('shake')},500);
clearInterval(fishingTimer);
if(navigator.vibrate)navigator.vibrate([200,100,200]);
var msg=d.attacker+' attacked you! ';
if(d.success)msg+='Lost '+d.stolen+' fish.';
else msg+='Attack failed!';
showToast(msg,4000);
});

socket.on('trade_completed',function(d){
showToast('Trade completed with '+d.accepter,3000);
});

socket.on('game_reset',function(){
	var qr=E("qr-img");if(qr){qr.width=280;qr.height=280};var ec=E("event-card");if(ec)ec.classList.remove("big")
location.reload();
});

function applyState(d){
if(!d||!d.me)return;
myState=d;
var m=d.me;
document.getElementById('r-satiety').textContent=m.satiety;
document.getElementById('r-fish').textContent=m.fish;
document.getElementById('r-gold').textContent=m.gold;
document.getElementById('r-boat').textContent='Lv'+m.boat_level+' '+m.boat_name;
document.getElementById('day-badge').textContent='Day '+(d.world?d.world.day:0);
globalTimeRemaining=(d.world||{}).time_remaining||200;
updateClock();
isFishing=m.is_fishing;
fishingStartGlobal=m.fishing_start;
fishingDuration=m.fishing_duration||20;
fishingClicksPerFish=m.clicks_per_fish||8;var tripCost=m.trip_cost||5;
var fb=document.getElementById('btn-fish');if(fb)fb.lastChild.textContent='Fish('+tripCost+'g)';var fi=document.getElementById('fish-click-info');if(fi)fi.textContent=fishingClicksPerFish+' clicks = 1 fish';

// Info banner
var banner=document.getElementById('info-banner');
var pe=m.personal_event||{};var parts=[];
if(pe.icon)parts.push(pe.icon+' '+pe.name+': '+pe.desc);if(parts.length>0){banner.style.display='block';banner.textContent='Events: '+parts.join(' | ')}
else{banner.style.display='none'}


// Fishing overlay
if(m.is_fishing&&!document.getElementById('fishing-overlay').classList.contains('active')){
	sock_clicks=0;document.getElementById('fish-catch-now').textContent='0';var ta=document.getElementById('fish-tap-area');if(ta){ta.onclick=handleFishingTap;ta.ontouchstart=function(e){e.preventDefault();handleFishingTap(e.touches[0])}}
document.getElementById('fishing-overlay').classList.add('active');
if(!fishingTimer)fishingTimer=setInterval(updateFishingProgress,500);
}
if(!m.is_fishing&&document.getElementById('fishing-overlay').classList.contains('active')){
	var ta=document.getElementById('fish-tap-area');if(ta){ta.onclick=null;ta.ontouchstart=null};sock_clicks=0;
document.getElementById('fishing-overlay').classList.remove('active');
clearInterval(fishingTimer);fishingTimer=null;
}

// Button states
var busy=m.busy_until>0&&globalTimeRemaining>m.busy_until;
var cooldown=m.attack_cooldown>0;
var eliminated=m.eliminated;
var btns={
'btn-fish':m.is_fishing||busy||eliminated,
'btn-attack':busy||m.is_fishing||cooldown||eliminated,
'btn-trade':busy||m.is_fishing||eliminated,
'btn-eat':m.fish<=0||m.satiety>=5||m.eat_count_today>=5||busy||eliminated,
'btn-sell':m.fish<=0||busy||eliminated,
'btn-rest':eliminated,
'btn-upgrade':m.boat_level>=4||m.is_fishing||busy||eliminated,
};
for(var id in btns){
document.getElementById(id).classList.toggle('disabled',btns[id]);
}
if(m.boat_level<4){
var cost={1:0,2:80,3:200,4:500}[m.boat_level+1];
document.getElementById('btn-upgrade').querySelector('.btn-icon').textContent='⬆️';
document.getElementById('btn-upgrade').lastChild.textContent='Upgrade('+cost+'g)';
}

// Trade orders
var ordersDiv=document.getElementById('trade-orders');
if(ordersDiv&&d.trade_orders){
if(d.trade_orders.length===0){
ordersDiv.innerHTML='<p style="color:#5e5d59">No active orders</p>';
}else{
ordersDiv.innerHTML=d.trade_orders.map(function(o){
var label=o.type==='sell'?'SELL '+o.fish_amount+' fish for '+o.gold_amount+' gold':'BUY '+o.fish_amount+' fish for '+o.gold_amount+' gold';
return '<div class="trade-order"><div class="tinfo"><div><strong>'+o.player_name+'</strong><br><span style="font-size:12px;color:#5e5d59">'+label+'</span></div><button onclick="acceptTrade(\''+o.id+'\')">Accept</button></div></div>';
}).join('');
}
}

// Attack targets
var targetsDiv=document.getElementById('attack-targets');
if(targetsDiv&&d.other_players){
if(d.other_players.length===0){
targetsDiv.innerHTML='<p style="color:#5e5d59">No other players available</p>';
}else{
targetsDiv.innerHTML=d.other_players.map(function(p,i){
return '<div class="player-target" onclick="doAttack(\''+p.sid+'\',\''+p.name+'\')"><div><span class="pname">'+p.name+'</span><br><span class="pinfo">'+p.boat_name+(p.is_fishing?' | 🎣 Fishing':'')+'</span></div><span style="font-size:24px">⚔️</span></div>';
}).join('');
}
}
}

function startFishing(){
setStatus('Sending...',false);
sock_clicks=0;
socket.emit('player_action',{action:'start_fishing'});
}

function cancelFishing(){
setStatus('Returning...',false);
socket.emit('player_action',{action:'cancel_fishing'});
clearInterval(fishingTimer);fishingTimer=null;
isFishing=false;
sock_clicks=0;
document.getElementById('fishing-overlay').classList.remove('active');
}

function openAttackModal(){
setStatus('Loading targets...',false);
socket.emit('player_action',{action:'get_state'});
document.getElementById('attack-modal').classList.add('active');
}

function doAttack(targetSid,targetName){
if(!confirm('Attack '+targetName+'?'))return;
setStatus('Attacking...',false);
socket.emit('player_action',{action:'attack',target_sid:targetSid});
closeModal('attack-modal');
}

function openTradeModal(){
setStatus('Loading market...',false);
socket.emit('player_action',{action:'get_state'});
document.getElementById('trade-modal').classList.add('active');
}

function createTrade(){
setStatus('Posting trade...',false);
socket.emit('player_action',{action:'create_trade',type:document.getElementById('trade-type').value,fish:tradeFishVal,gold:tradeGoldVal});
closeModal('trade-modal');
}

function acceptTrade(orderId){
setStatus('Accepting trade...',false);
socket.emit('player_action',{action:'accept_trade',order_id:orderId});
closeModal('trade-modal');
}

function eatFish(){
setStatus('Eating...',false);
socket.emit('player_action',{action:'eat'});
}

var tradeFishVal=1,tradeGoldVal=1,sellAmount=1;

function adjTrade(what,d){
if(what==='fish'){tradeFishVal=Math.max(1,tradeFishVal+d);document.getElementById('trade-fish-val').textContent=tradeFishVal}
else{tradeGoldVal=Math.max(1,tradeGoldVal+d);document.getElementById('trade-gold-val').textContent=tradeGoldVal}
}

function adjSell(d){
var maxFish=myState&&myState.me?myState.me.fish:1;
if(d>=999)sellAmount=maxFish;
else sellAmount=Math.max(1,Math.min(maxFish,sellAmount+d));
document.getElementById('sell-amount-val').textContent=sellAmount;
if(myState&&myState.world){
document.getElementById('sell-total-info').textContent='Total: '+(sellAmount*myState.world.sell_price)+' gold';
}
}

function openSellModal(){
if(!myState||!myState.world)return;
sellAmount=Math.min(myState.me.fish,5);
document.getElementById('sell-amount-val').textContent=sellAmount;
document.getElementById('sell-price-info').textContent='Price: '+myState.world.sell_price+' gold/fish';
document.getElementById('sell-total-info').textContent='Total: '+(sellAmount*myState.world.sell_price)+' gold';
document.getElementById('sell-modal').classList.add('active');
}

function confirmSell(){
setStatus('Selling...',false);
socket.emit('player_action',{action:'sell_fish',amount:sellAmount});
closeModal('sell-modal');
}

function sellFish(){openSellModal();}

function upgradeBoat(){
setStatus('Upgrading...',false);
socket.emit('player_action',{action:'upgrade_boat'});
}

function doRest(){
if(isFishing){cancelFishing();return}
setStatus('Resting...',false);
socket.emit('player_action',{action:'rest'});
}

function closeModal(id){document.getElementById(id).classList.remove('active')}
</script>
</body>
</html>'''

# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    port = int(os.environ.get("PORT", 5000))

    local_ip = get_local_ip()
    print("=" * 60)
    print("  🎣 Fish & Chips & Shipwreck - Multiplayer Game")
    print("=" * 60)
    print()
    print(f"  Teacher screen:  http://{local_ip}:{port}")
    print(f"  Player join:     http://{local_ip}:{port}/join")
    print()
    print("  Make sure both devices are on the same WiFi network.")
    print("  Press Ctrl+C to stop the server.")
    print()
    print("=" * 60)

    # Start background tick
    socketio.start_background_task(game._tick_loop)

    socketio.run(app, host="0.0.0.0", port=port, debug=False, allow_unsafe_werkzeug=True)
