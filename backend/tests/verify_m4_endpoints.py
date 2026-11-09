"""
End-to-end verification script for Milestone 4 (E2E Integration & Verification).
Tests:
1. GET /api/status (with lifespan/scheduler active)
2. GET /api/market/option-chain?symbol=BANKNIFTY
3. POST /api/trades/manual with lots: 2 (strictly HTTP 400 Bad Request)
"""
import asyncio
import re
import sys
import httpx
from httpx import ASGITransport

from main import app
from scheduler import start_scheduler, stop_scheduler


async def verify_endpoints():
    print("=================================================================")
    print("Running Milestone 4 Service Health & Status Verification...")
    print("=================================================================")

    start_scheduler()
    try:
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            # 1. GET /api/status
            print("\n--- 1. Testing GET /api/status ---")
            resp_status = await client.get("/api/status")
            print(f"Status Code: {resp_status.status_code}")
            assert resp_status.status_code == 200, f"Expected 200, got {resp_status.status_code}"
            data_status = resp_status.json()
            print(f"Response: {data_status}")

            dhan_status = data_status.get("dhan_status")
            print(f"  dhan_status: {dhan_status}")
            assert dhan_status in ("LIVE", "FALLBACK", "DISCONNECTED"), f"Invalid dhan_status: {dhan_status}"

            nifty_spot = data_status.get("nifty_spot")
            print(f"  nifty_spot: {nifty_spot} (type: {type(nifty_spot)})")
            assert isinstance(nifty_spot, (int, float)), f"nifty_spot not numeric: {nifty_spot}"

            banknifty_spot = data_status.get("banknifty_spot")
            print(f"  banknifty_spot: {banknifty_spot} (type: {type(banknifty_spot)})")
            assert isinstance(banknifty_spot, (int, float)), f"banknifty_spot not numeric: {banknifty_spot}"

            next_debate_ist = data_status.get("next_debate_ist")
            print(f"  next_debate_ist: {next_debate_ist}")
            assert "next_debate_ist" in data_status, "next_debate_ist key missing from response"
            if next_debate_ist is not None:
                assert ("+05:30" in next_debate_ist) or next_debate_ist.endswith(" IST"), f"Invalid next_debate_ist format: {next_debate_ist}"

            countdown_timer = data_status.get("countdown_timer")
            print(f"  countdown_timer: {countdown_timer}")
            assert countdown_timer is not None, "countdown_timer is None"
            timer_pattern = r"^(\d{2}:\d{2}|--:--)$"
            assert re.match(timer_pattern, countdown_timer), f"countdown_timer '{countdown_timer}' does not match pattern mm:ss or --:--"

            budget_limit = data_status.get("budget_limit")
            print(f"  budget_limit: {budget_limit}")
            assert budget_limit == 5000.0, f"Expected budget_limit 5000.0, got {budget_limit}"

            max_lots = data_status.get("max_lots")
            print(f"  max_lots: {max_lots}")
            assert max_lots == 1, f"Expected max_lots 1, got {max_lots}"

            print(">>> GET /api/status VERIFICATION SUCCESSFUL")

            # 2. GET /api/market/option-chain?symbol=BANKNIFTY
            print("\n--- 2. Testing GET /api/market/option-chain?symbol=BANKNIFTY ---")
            resp_chain = await client.get("/api/market/option-chain?symbol=BANKNIFTY")
            print(f"Status Code: {resp_chain.status_code}")
            assert resp_chain.status_code == 200, f"Expected 200, got {resp_chain.status_code}"
            data_chain = resp_chain.json()
            print(f"Symbol: {data_chain.get('symbol')}, Spot: {data_chain.get('spot_price')}, Lot Size: {data_chain.get('lot_size')}")

            lot_size = data_chain.get("lot_size")
            assert lot_size == 15, f"Expected lot_size 15 for BANKNIFTY, got {lot_size}"

            chain = data_chain.get("chain", [])
            strikes = [row["strike"] for row in chain]
            print(f"Returned Strikes ({len(strikes)}): {strikes}")
            assert len(strikes) >= 2, f"Option chain returned fewer than 2 strikes: {len(strikes)}"

            strike_diffs = [strikes[i+1] - strikes[i] for i in range(len(strikes)-1)]
            print(f"Strike increments between consecutive rows: {strike_diffs}")
            assert all(diff == 100 for diff in strike_diffs), f"Strike step for Bank Nifty must be exactly 100, got {strike_diffs}"

            print(">>> GET /api/market/option-chain?symbol=BANKNIFTY VERIFICATION SUCCESSFUL")

            # 3. POST /api/trades/manual with lots: 2
            print("\n--- 3. Testing POST /api/trades/manual with lots: 2 ---")
            manual_payload = {
                "symbol": "NIFTY",
                "strike": 25000,
                "option_type": "CE",
                "lots": 2,
                "entry_price": 50.0
            }
            print(f"Submitting payload: {manual_payload}")
            resp_manual = await client.post("/api/trades/manual", json=manual_payload)
            print(f"Status Code: {resp_manual.status_code}")
            print(f"Response: {resp_manual.json()}")
            assert resp_manual.status_code == 400, f"Expected HTTP 400 Bad Request, got {resp_manual.status_code}"
            detail_msg = resp_manual.json().get("detail", "")
            print(f"Error detail message: '{detail_msg}'")
            assert "1-lot" in detail_msg or "lots" in detail_msg, f"Expected message mentioning 1-lot restriction, got: {detail_msg}"

            print(">>> POST /api/trades/manual (lots=2) VERIFICATION SUCCESSFUL")

        print("\n=================================================================")
        print("ALL ENDPOINT VERIFICATIONS PASSED CLEANLY!")
        print("=================================================================")
    finally:
        stop_scheduler()


if __name__ == "__main__":
    asyncio.run(verify_endpoints())
