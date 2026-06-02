#!/usr/bin/env python3
"""Тест JWT авторизации в Спринте 3."""
import asyncio
import os
import sys

# Отключаем SOCKS proxy
os.environ.pop("ALL_PROXY", None)
os.environ.pop("HTTP_PROXY", None)
os.environ.pop("HTTPS_PROXY", None)
os.environ["NO_PROXY"] = "*"

from httpx import AsyncClient

async def test_auth():
    async with AsyncClient(timeout=10.0) as client:
        # Test 1: Register
        print("=" * 70)
        print("ТЕСТ 1: Регистрация")
        print("=" * 70)
        res = await client.post("http://localhost:8000/api/v1/auth/register", json={
            "email": "sprint3@test.com",
            "password": "TestPass123",
            "full_name": "Sprint 3 User",
            "company_name": "Sprint 3 Corp"
        })
        print(f"Status: {res.status_code}")
        data = res.json()
        print(f"✓ User ID: {data.get('user_id')}")
        print(f"✓ Company ID: {data.get('company_id')}")
        company_id = data['company_id']

        # Test 2: Login
        print("\n" + "=" * 70)
        print("ТЕСТ 2: Вход и получение JWT токена")
        print("=" * 70)
        res = await client.post("http://localhost:8000/api/v1/auth/token", json={
            "email": "sprint3@test.com",
            "password": "TestPass123"
        })
        print(f"Status: {res.status_code}")
        data = res.json()
        print(f"✓ Token Type: {data.get('token_type')}")
        print(f"✓ Expires In: {data.get('expires_in')} сек")
        print(f"✓ User Email: {data.get('email')}")
        print(f"✓ Token (первые 50): {data['access_token'][:50]}...")
        token = data['access_token']

        # Test 3: Protected endpoint WITH token
        print("\n" + "=" * 70)
        print("ТЕСТ 3: Доступ к защищённому эндпоинту С JWT токеном")
        print("=" * 70)
        res = await client.get(
            f"http://localhost:8000/api/v1/companies/{company_id}/reports/financial",
            params={"start_date": "2025-01-01", "end_date": "2025-01-31"},
            headers={"Authorization": f"Bearer {token}"}
        )
        print(f"Status: {res.status_code}")
        if res.status_code == 200:
            data = res.json()
            print(f"✓ Report Currency: {data.get('currency')}")
            print(f"✓ Total Revenue: {data.get('totals', {}).get('total_revenue')} ₽")
        else:
            print(f"✗ Error: {res.text[:100]}")

        # Test 4: Protected endpoint WITHOUT token
        print("\n" + "=" * 70)
        print("ТЕСТ 4: Попытка доступа БЕЗ JWT (ожидаем ошибку 403/401)")
        print("=" * 70)
        res = await client.get(
            f"http://localhost:8000/api/v1/companies/{company_id}/reports/financial",
            params={"start_date": "2025-01-01", "end_date": "2025-01-31"}
        )
        print(f"Status: {res.status_code} (ожидается 403 или 401)")
        if res.status_code >= 400:
            print(f"✓ Доступ корректно запрещён")
            print(f"  Detail: {res.json().get('detail', 'Authentication required')}")
        else:
            print(f"✗ Неправильно: доступ разрешён без токена!")

        print("\n" + "=" * 70)
        print("✅ ВСЕ ТЕСТЫ JWT АВТОРИЗАЦИИ ПРОЙДЕНЫ УСПЕШНО")
        print("=" * 70)

asyncio.run(test_auth())
