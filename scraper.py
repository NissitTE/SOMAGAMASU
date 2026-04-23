import requests
from bs4 import BeautifulSoup
import re
from datetime import datetime
from difflib import SequenceMatcher

class SomagamasuScraper:
    def __init__(self):
        self.session = requests.Session()
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Cookie": "birthtime=568022401; lastagecheckage=1-0-1988; wants_mature_content=1",
            "Accept-Language": "th-TH,th;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        self._cs_stores: dict = {}  # CheapShark store cache

    # ============================================================
    # Currency config: lang → (steam_cc, steam_lang, symbol)
    # ============================================================
    LANG_CURRENCY = {
        "english":  ("us", "english",  "$"),
        "thai":     ("th", "thai",     "฿"),
        "french":   ("fr", "french",   "€"),
        "spanish":  ("es", "spanish",  "€"),
        "japanese": ("jp", "japanese", "¥"),
        "schinese": ("cn", "schinese", "¥"),
    }

    # ============================================================
    # FUZZY MATCH
    # ============================================================
    def _fuzzy_score(self, query: str, title: str) -> float:
        q = query.lower().strip()
        t = title.lower().strip()
        if q == t: return 1.0
        if q in t: return 0.9
        q_tokens = set(q.split())
        t_tokens = set(t.split())
        token_overlap = len(q_tokens & t_tokens) / max(len(q_tokens), 1)
        seq_score = SequenceMatcher(None, q, t).ratio()
        partial = max((SequenceMatcher(None, q, tok).ratio() for tok in t_tokens), default=0)
        return max(token_overlap * 0.7 + seq_score * 0.3, partial * 0.6)

    # ============================================================
    # 1. STEAM SEARCH
    # ============================================================
    def fetch_deals(self, query, lang="english"):
        cc, steam_lang, symbol = self.LANG_CURRENCY.get(lang, ("us", "english", "$"))
        url = (
            f"https://store.steampowered.com/search/"
            f"?term={requests.utils.quote(query)}"
            f"&category1=998%2C994&cc={cc}&l={steam_lang}"
        )
        try:
            res = self.session.get(url, headers=self.headers, timeout=10)
            soup = BeautifulSoup(res.text, 'html.parser')
            items = soup.select('#search_resultsRows a')[:90]

            results = []
            for item in items:
                title_el = item.find('span', class_='title')
                if not title_el:
                    continue
                title = title_el.text.strip()
                href = item.get('href', '')

                score = self._fuzzy_score(query, title)
                if score < 0.25:
                    continue

                app_id_match = re.search(r'/app/(\d+)/', href)
                app_id = app_id_match.group(1) if app_id_match else None

                img_el = item.find('img')
                img = img_el['src'].replace('capsule_sm_120', 'header') if img_el else ''

                price_val, original_price, discount_pct = self._parse_price(item)

                rel_el = item.find('div', class_='search_released')
                release = rel_el.text.strip() if rel_el else "TBA"

                results.append({
                    "app_id":         app_id,
                    "name":           title,
                    "img":            img,
                    "price":          price_val,
                    "original_price": original_price,
                    "discount":       discount_pct,
                    "dev":            "",
                    "publisher":      "",
                    "genre":          "",
                    "link":           href,
                    "release":        release,
                    "sale_type":      "",
                    "other_stores":   [],
                    "metacritic":     None,
                    "_score":         score,
                })

            results = self._sort_results(results, query)

            for r in results[:30]:
                if r["app_id"]:
                    self._enrich_app_details(r, cc=cc, symbol=symbol)
                    r["other_stores"] = self._fetch_other_stores(r["name"], r["app_id"])

            return results

        except Exception as e:
            print(f"[scraper] fetch_deals error: {e}")
            return []

    # ============================================================
    # 2. PARSE PRICE
    # ============================================================
    def _parse_price(self, item):
        price_val = "Check Store"
        original_price = ""
        discount_pct = 0

        final_el = item.find('div', class_='discount_final_price')
        orig_el  = item.find('div', class_='discount_original_price')
        if final_el:
            raw = final_el.get_text(strip=True)
            price_val = "Free to Play" if any(w in raw.lower() for w in ["free","ฟรี"]) else (raw or "Check Store")
            if orig_el:
                original_price = orig_el.get_text(strip=True)
        else:
            price_div = item.find('div', class_='search_price')
            if price_div:
                raw_text = price_div.get_text(strip=True)
                if any(w in raw_text.lower() for w in ["free", "ฟรี"]):
                    price_val = "Free to Play"
                else:
                    matches = re.findall(r"[฿$€£¥]\s?[\d,]+(?:\.\d+)?", raw_text)
                    if len(matches) >= 2:
                        original_price = matches[0]
                        price_val = matches[-1]
                    elif len(matches) == 1:
                        price_val = matches[0]
                    else:
                        nums = re.findall(r"[\d,]+(?:\.\d+)?", raw_text)
                        if nums:
                            price_val = f"{nums[-1]}"

        disc_div = item.find('div', class_='search_discount') or item.find('div', class_='discount_pct')
        if disc_div and disc_div.text.strip():
            try:
                discount_pct = int(re.sub(r'[^0-9]', '', disc_div.text.strip()))
            except:
                discount_pct = 0

        return price_val, original_price, discount_pct

    # ============================================================
    # 3. ENRICH — Steam API
    # ============================================================
    def _enrich_app_details(self, item, cc="us", symbol="$"):
        app_id = item["app_id"]
        try:
            api_url = f"https://store.steampowered.com/api/appdetails?appids={app_id}&cc={cc}&l=english"
            r = self.session.get(api_url, headers=self.headers, timeout=8)
            data = r.json()
            app_data = data.get(str(app_id), {}).get("data", {})
            if not app_data:
                return

            devs = app_data.get("developers", [])
            pubs = app_data.get("publishers", [])
            item["dev"]       = ", ".join(devs) if devs else "Unknown"
            item["publisher"] = ", ".join(pubs) if pubs else ""

            genres = app_data.get("genres", [])
            item["genre"] = ", ".join([g["description"] for g in genres]) if genres else "Uncategorized"

            cats = app_data.get("categories", [])
            item["categories"] = ", ".join([c["description"] for c in cats[:4]]) if cats else ""

            meta = app_data.get("metacritic", {})
            item["metacritic"] = meta.get("score", None)

            # Fallback ราคา — Steam API ส่งราคาตาม cc + symbol อัตโนมัติ
            if item["price"] == "Check Store":
                po = app_data.get("price_overview", {})
                if po:
                    item["price"]          = po.get("final_formatted", "Check Store")
                    item["original_price"] = po.get("initial_formatted", "")
                    item["discount"]       = po.get("discount_percent", 0)
                elif app_data.get("is_free"):
                    item["price"] = "Free to Play"

        except Exception as e:
            print(f"[scraper] enrich error app_id={app_id}: {e}")

        try:
            item["sale_type"] = self._calc_sale_type(item["discount"], item.get("release", ""))
        except:
            pass

    # ============================================================
    # 4. SALE TYPE
    # ============================================================
    def _calc_sale_type(self, discount_pct, release_str):
        if not discount_pct or discount_pct == 0:
            return ""
        years_old = 0
        for fmt in ["%b %Y", "%d %b, %Y", "%Y", "%d %b %Y"]:
            try:
                dt = datetime.strptime(release_str.strip(), fmt)
                years_old = (datetime.now() - dt).days / 365
                break
            except:
                continue
        d = int(discount_pct)
        if years_old >= 5 and d >= 50: return "forever"
        if years_old >= 5 and d >= 30: return "legendary"
        if years_old >= 3 and d >= 40: return "rare"
        if d >= 20:                    return "sale"
        return ""

    # ============================================================
    # 5. OTHER STORES — CheapShark API (ฟรี ไม่ต้อง Key)
    # ============================================================
    STORE_ICON = {
        "steam":            "🎮",
        "gog":              "🌌",
        "humble store":     "🎁",
        "humble":           "🎁",
        "fanatical":        "🔥",
        "green man gaming": "🟢",
        "gmg":              "🟢",
        "epic games store": "⚡",
        "epic games":       "⚡",
        "epic":             "⚡",
        "gamebillet":       "🎟️",
        "wingamestore":     "🏪",
        "2game":            "2️⃣",
        "indiegala":        "🌀",
        "voidu":            "🔵",
        "gamesplanet":      "🪐",
        "dlgamer":          "🎲",
        "nuuvem":           "🌿",
        "eneba":            "🛒",
        "kinguin":          "👑",
        "g2a":              "🅶",
        "cdkeys":           "🔑",
        "microsoft":        "🪟",
        "xbox":             "🎯",
    }

    def _load_cheapshark_stores(self):
        if self._cs_stores:
            return
        try:
            r = self.session.get("https://www.cheapshark.com/api/1.0/stores", timeout=6)
            for s in r.json():
                sid  = str(s.get("storeID", ""))
                name = s.get("storeName", "Unknown")
                self._cs_stores[sid] = name
        except Exception as e:
            print(f"[scraper] CheapShark stores load error: {e}")

    def _store_icon(self, name: str) -> str:
        nl = name.lower()
        for key, icon in self.STORE_ICON.items():
            if key in nl:
                return icon
        return "🏬"

    def _fetch_other_stores(self, game_name: str, app_id: str) -> list:
        self._load_cheapshark_stores()
        stores = []
        try:
            r = self.session.get(
                f"https://www.cheapshark.com/api/1.0/games"
                f"?title={requests.utils.quote(game_name)}&limit=5&exact=0",
                timeout=8
            )
            games = r.json()
            if not games:
                raise ValueError("no games found")

            best = max(
                games,
                key=lambda g: SequenceMatcher(None, game_name.lower(), g.get("external","").lower()).ratio()
            )
            game_id = best.get("gameID")
            if not game_id:
                raise ValueError("no gameID")

            r2 = self.session.get(f"https://www.cheapshark.com/api/1.0/games?id={game_id}", timeout=8)
            data = r2.json()

            for deal in data.get("deals", []):
                store_id   = str(deal.get("storeID", ""))
                store_name = self._cs_stores.get(store_id, f"Store {store_id}")
                price_usd  = deal.get("price", "?")
                retail_usd = deal.get("retailPrice", "")
                savings    = deal.get("savings", "0")

                try:
                    disc_pct = int(float(savings))
                except:
                    disc_pct = 0

                deal_id = deal.get("dealID", "")
                buy_url = f"https://www.cheapshark.com/redirect?dealID={deal_id}" if deal_id else "#"

                try:
                    price_usd_f  = float(price_usd)
                    retail_usd_f = float(retail_usd) if retail_usd and retail_usd != "0" else None
                except:
                    price_usd_f  = None
                    retail_usd_f = None

                stores.append({
                    "store":          store_name,
                    "icon":           self._store_icon(store_name),
                    "price_usd":      price_usd_f,   # ส่ง float USD ให้ frontend แปลง
                    "retail_usd":     retail_usd_f,
                    "price":          f"${price_usd_f:.2f}" if price_usd_f is not None else "?",
                    "original_price": f"${retail_usd_f:.2f}" if retail_usd_f else "",
                    "discount":       disc_pct,
                    "url":            buy_url,
                })

            stores.sort(key=lambda x: (-x["discount"], x["price"]))

        except Exception as e:
            print(f"[scraper] CheapShark error for '{game_name}': {e}")

        if not stores and app_id:
            stores.append({
                "store":          "Steam",
                "icon":           "🎮",
                "price_usd":      None,
                "retail_usd":     None,
                "price":          "—",
                "original_price": "",
                "discount":       0,
                "url":            f"https://store.steampowered.com/app/{app_id}/",
            })

        return stores

    # ============================================================
    # 6. SORT
    # ============================================================
    def _sort_results(self, results, query):
        dlc_kw = ["dlc","pack","bundle","soundtrack","ost","content",
                  "expansion","skin","costume","season pass","artbook"]
        def sort_key(item):
            is_dlc = any(k in item["name"].lower() for k in dlc_kw)
            return (is_dlc, -round(item.get("_score", 0), 2), item["name"].lower())
        return sorted(results, key=sort_key)
