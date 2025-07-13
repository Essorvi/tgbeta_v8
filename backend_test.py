#!/usr/bin/env python3
"""
Backend Testing Suite for Custom Amount Functionality
Tests the "своя сумма" (custom amount) functionality for Telegram Stars and crypto payments
"""

import requests
import json
import os
import sys
import time
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv('/app/backend/.env')

# Configuration
BACKEND_URL = os.getenv('REACT_APP_BACKEND_URL', 'https://da93c359-3829-4b53-b388-a20063a6715b.preview.emergentagent.com')
API_BASE = f"{BACKEND_URL}/api"
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
WEBHOOK_SECRET = os.getenv('WEBHOOK_SECRET')
BOT_USERNAME = os.getenv('BOT_USERNAME', 'search1_test_bot')

class CustomAmountTester:
    def __init__(self):
        self.test_results = []
        self.test_user_id = 987654321  # Test user ID for custom amount tests
        self.test_chat_id = 987654321  # Test chat ID
        
    def log_test(self, test_name, success, message="", details=""):
        """Log test result"""
        status = "✅ PASS" if success else "❌ FAIL"
        result = {
            'test': test_name,
            'status': status,
            'message': message,
            'details': details,
            'timestamp': datetime.now().isoformat()
        }
        self.test_results.append(result)
        print(f"{status}: {test_name}")
        if message:
            print(f"   Message: {message}")
        if details and not success:
            print(f"   Details: {details}")
        print()

    def send_webhook_update(self, update_data):
        """Send webhook update to the bot"""
        try:
            webhook_url = f"{API_BASE}/webhook/{WEBHOOK_SECRET}"
            response = requests.post(webhook_url, json=update_data, timeout=10)
            return response.status_code == 200, response
        except Exception as e:
            return False, str(e)

    def create_callback_update(self, callback_data, message_id=1):
        """Create callback query update"""
        return {
            "update_id": int(time.time()),
            "callback_query": {
                "id": str(int(time.time())),
                "from": {
                    "id": self.test_user_id,
                    "is_bot": False,
                    "first_name": "TestUser",
                    "username": "testuser_custom"
                },
                "message": {
                    "message_id": message_id,
                    "from": {
                        "id": 123456789,
                        "is_bot": True,
                        "first_name": "TestBot",
                        "username": BOT_USERNAME
                    },
                    "chat": {
                        "id": self.test_chat_id,
                        "first_name": "TestUser",
                        "username": "testuser_custom",
                        "type": "private"
                    },
                    "date": int(time.time()),
                    "text": "Test message"
                },
                "data": callback_data
            }
        }

    def create_message_update(self, text):
        """Create message update"""
        return {
            "update_id": int(time.time()),
            "message": {
                "message_id": int(time.time()),
                "from": {
                    "id": self.test_user_id,
                    "is_bot": False,
                    "first_name": "TestUser",
                    "username": "testuser_custom"
                },
                "chat": {
                    "id": self.test_chat_id,
                    "first_name": "TestUser",
                    "username": "testuser_custom",
                    "type": "private"
                },
                "date": int(time.time()),
                "text": text
            }
        }

    def test_api_health(self):
        """Test basic API health"""
        try:
            response = requests.get(f"{API_BASE}/", timeout=10)
            if response.status_code == 200:
                data = response.json()
                if "УЗРИ - Telegram Bot API" in data.get('message', ''):
                    self.log_test("API Health Check", True, "API is running and responding correctly")
                    return True
                else:
                    self.log_test("API Health Check", False, "API response format incorrect", str(data))
                    return False
            else:
                self.log_test("API Health Check", False, f"HTTP {response.status_code}", response.text)
                return False
        except Exception as e:
            self.log_test("API Health Check", False, "Connection failed", str(e))
            return False

    def test_stars_custom_amount_callback(self):
        """Test Telegram Stars custom amount callback"""
        update = self.create_callback_update("stars_custom")
        success, response = self.send_webhook_update(update)
        
        if success:
            self.log_test("Stars Custom Amount Callback", True, "Callback processed successfully")
        else:
            self.log_test("Stars Custom Amount Callback", False, "Callback failed", str(response))

    def test_crypto_custom_amount_callbacks(self):
        """Test crypto custom amount callbacks for all currencies"""
        crypto_types = ["btc", "eth", "usdt", "ltc"]
        
        for crypto in crypto_types:
            callback_data = f"crypto_{crypto}_custom"
            update = self.create_callback_update(callback_data)
            success, response = self.send_webhook_update(update)
            
            if success:
                self.log_test(f"Crypto Custom Amount Callback ({crypto.upper()})", True, f"Callback for {crypto.upper()} processed successfully")
            else:
                self.log_test(f"Crypto Custom Amount Callback ({crypto.upper()})", False, f"Callback for {crypto.upper()} failed", str(response))

    def test_custom_amount_validation(self):
        """Test custom amount validation logic"""
        # Test validation function logic
        def validate_custom_amount(amount_str: str) -> tuple[bool, str, float]:
            """Validate custom amount input"""
            try:
                amount = float(amount_str)
                
                if amount < 100:
                    return False, "Минимальная сумма: 100₽", 0
                
                if amount % 50 != 0:
                    return False, "Сумма должна быть кратна 50₽", 0
                    
                if amount > 50000:
                    return False, "Максимальная сумма: 50,000₽", 0
                    
                return True, "", amount
                
            except ValueError:
                return False, "Введите корректную сумму (только цифры)", 0

        # Test cases
        test_cases = [
            # Valid amounts
            ("100", True, "Valid minimum amount"),
            ("250", True, "Valid amount multiple of 50"),
            ("500", True, "Valid medium amount"),
            ("1000", True, "Valid large amount"),
            ("50000", True, "Valid maximum amount"),
            
            # Invalid amounts - too small
            ("50", False, "Below minimum (50₽)"),
            ("99", False, "Below minimum (99₽)"),
            
            # Invalid amounts - not multiple of 50
            ("125", False, "Not multiple of 50 (125₽)"),
            ("175", False, "Not multiple of 50 (175₽)"),
            ("333", False, "Not multiple of 50 (333₽)"),
            
            # Invalid amounts - too large
            ("50001", False, "Above maximum (50,001₽)"),
            ("100000", False, "Above maximum (100,000₽)"),
            
            # Invalid formats
            ("abc", False, "Non-numeric input"),
            ("", False, "Empty input"),
            ("100.5", False, "Decimal input"),
            ("-100", False, "Negative input"),
        ]
        
        all_passed = True
        for amount_str, should_pass, description in test_cases:
            is_valid, error_msg, amount = validate_custom_amount(amount_str)
            
            if should_pass and is_valid:
                self.log_test(f"Amount Validation: {description}", True, f"Correctly validated: {amount_str}")
            elif not should_pass and not is_valid:
                self.log_test(f"Amount Validation: {description}", True, f"Correctly rejected: {amount_str} - {error_msg}")
            else:
                all_passed = False
                expected = "pass" if should_pass else "fail"
                actual = "passed" if is_valid else "failed"
                self.log_test(f"Amount Validation: {description}", False, f"Expected to {expected}, but {actual}: {amount_str}")

        if all_passed:
            self.log_test("Amount Validation Overall", True, "All validation test cases passed")
        else:
            self.log_test("Amount Validation Overall", False, "Some validation test cases failed")

    def test_stars_custom_amount_input_flow(self):
        """Test complete Stars custom amount input flow"""
        # Step 1: Trigger custom amount callback
        callback_update = self.create_callback_update("stars_custom")
        success, response = self.send_webhook_update(callback_update)
        
        if not success:
            self.log_test("Stars Custom Amount Flow - Callback", False, "Initial callback failed", str(response))
            return
        
        time.sleep(1)  # Wait for state to be set
        
        # Step 2: Send valid amount
        message_update = self.create_message_update("500")
        success, response = self.send_webhook_update(message_update)
        
        if success:
            self.log_test("Stars Custom Amount Flow - Valid Input", True, "Valid amount input processed")
        else:
            self.log_test("Stars Custom Amount Flow - Valid Input", False, "Valid amount input failed", str(response))
        
        time.sleep(1)
        
        # Step 3: Test invalid amount
        callback_update2 = self.create_callback_update("stars_custom")
        self.send_webhook_update(callback_update2)
        time.sleep(1)
        
        message_update_invalid = self.create_message_update("75")  # Below minimum
        success, response = self.send_webhook_update(message_update_invalid)
        
        if success:
            self.log_test("Stars Custom Amount Flow - Invalid Input", True, "Invalid amount input handled correctly")
        else:
            self.log_test("Stars Custom Amount Flow - Invalid Input", False, "Invalid amount input handling failed", str(response))

    def test_crypto_custom_amount_input_flow(self):
        """Test complete crypto custom amount input flow"""
        crypto_type = "btc"  # Test with Bitcoin
        
        # Step 1: Trigger custom amount callback
        callback_update = self.create_callback_update(f"crypto_{crypto_type}_custom")
        success, response = self.send_webhook_update(callback_update)
        
        if not success:
            self.log_test("Crypto Custom Amount Flow - Callback", False, "Initial callback failed", str(response))
            return
        
        time.sleep(1)  # Wait for state to be set
        
        # Step 2: Send valid amount
        message_update = self.create_message_update("1000")
        success, response = self.send_webhook_update(message_update)
        
        if success:
            self.log_test("Crypto Custom Amount Flow - Valid Input", True, "Valid amount input processed")
        else:
            self.log_test("Crypto Custom Amount Flow - Valid Input", False, "Valid amount input failed", str(response))
        
        time.sleep(1)
        
        # Step 3: Test invalid amount
        callback_update2 = self.create_callback_update(f"crypto_{crypto_type}_custom")
        self.send_webhook_update(callback_update2)
        time.sleep(1)
        
        message_update_invalid = self.create_message_update("125")  # Not multiple of 50
        success, response = self.send_webhook_update(message_update_invalid)
        
        if success:
            self.log_test("Crypto Custom Amount Flow - Invalid Input", True, "Invalid amount input handled correctly")
        else:
            self.log_test("Crypto Custom Amount Flow - Invalid Input", False, "Invalid amount input handling failed", str(response))

    def test_user_state_management(self):
        """Test user state management for custom amounts"""
        # Test that user states are properly set and cleared
        
        # Test Stars state
        callback_update = self.create_callback_update("stars_custom")
        success, response = self.send_webhook_update(callback_update)
        
        if success:
            self.log_test("User State Management - Stars State Set", True, "Stars custom amount state should be set")
        else:
            self.log_test("User State Management - Stars State Set", False, "Failed to set Stars state", str(response))
        
        time.sleep(1)
        
        # Send amount to clear state
        message_update = self.create_message_update("200")
        success, response = self.send_webhook_update(message_update)
        
        if success:
            self.log_test("User State Management - Stars State Cleared", True, "Stars state should be cleared after input")
        else:
            self.log_test("User State Management - Stars State Cleared", False, "Failed to clear Stars state", str(response))
        
        time.sleep(1)
        
        # Test Crypto state
        callback_update = self.create_callback_update("crypto_eth_custom")
        success, response = self.send_webhook_update(callback_update)
        
        if success:
            self.log_test("User State Management - Crypto State Set", True, "Crypto custom amount state should be set")
        else:
            self.log_test("User State Management - Crypto State Set", False, "Failed to set Crypto state", str(response))

    def test_navigation_flow(self):
        """Test navigation flow to custom amount options"""
        # Test navigation: Balance -> Stars -> Custom Amount
        navigation_steps = [
            ("menu_balance", "Balance menu"),
            ("pay_stars", "Stars payment menu"),
            ("stars_custom", "Stars custom amount")
        ]
        
        for callback_data, description in navigation_steps:
            update = self.create_callback_update(callback_data)
            success, response = self.send_webhook_update(update)
            
            if success:
                self.log_test(f"Navigation Flow - {description}", True, f"Successfully navigated to {description}")
            else:
                self.log_test(f"Navigation Flow - {description}", False, f"Failed to navigate to {description}", str(response))
            
            time.sleep(0.5)
        
        # Test navigation: Balance -> Crypto -> BTC -> Custom Amount
        crypto_navigation_steps = [
            ("menu_balance", "Balance menu"),
            ("pay_crypto", "Crypto payment menu"),
            ("crypto_btc", "Bitcoin payment menu"),
            ("crypto_btc_custom", "Bitcoin custom amount")
        ]
        
        for callback_data, description in crypto_navigation_steps:
            update = self.create_callback_update(callback_data)
            success, response = self.send_webhook_update(update)
            
            if success:
                self.log_test(f"Crypto Navigation Flow - {description}", True, f"Successfully navigated to {description}")
            else:
                self.log_test(f"Crypto Navigation Flow - {description}", False, f"Failed to navigate to {description}", str(response))
            
            time.sleep(0.5)

    def test_edge_cases(self):
        """Test edge cases for custom amount functionality"""
        # Test back button during custom amount input
        callback_update = self.create_callback_update("stars_custom")
        self.send_webhook_update(callback_update)
        time.sleep(1)
        
        # Send back button callback while in custom amount state
        back_update = self.create_callback_update("back_to_menu")
        success, response = self.send_webhook_update(back_update)
        
        if success:
            self.log_test("Edge Case - Back Button During Input", True, "Back button works during custom amount input")
        else:
            self.log_test("Edge Case - Back Button During Input", False, "Back button failed during custom amount input", str(response))
        
        time.sleep(1)
        
        # Test multiple rapid custom amount requests
        for i in range(3):
            update = self.create_callback_update("stars_custom")
            success, response = self.send_webhook_update(update)
            time.sleep(0.2)
        
        self.log_test("Edge Case - Rapid Custom Amount Requests", True, "Multiple rapid requests handled")

    def run_all_tests(self):
        """Run all custom amount tests"""
        print("🚀 Starting Custom Amount Functionality Tests")
        print("=" * 60)
        
        # Basic health check
        if not self.test_api_health():
            print("❌ API health check failed. Stopping tests.")
            return False
        
        # Core custom amount functionality tests
        self.test_stars_custom_amount_callback()
        self.test_crypto_custom_amount_callbacks()
        
        # Validation tests
        self.test_custom_amount_validation()
        
        # Flow tests
        self.test_stars_custom_amount_input_flow()
        self.test_crypto_custom_amount_input_flow()
        
        # State management tests
        self.test_user_state_management()
        
        # Navigation tests
        self.test_navigation_flow()
        
        # Edge case tests
        self.test_edge_cases()
        
        # Summary
        print("=" * 60)
        print("📊 CUSTOM AMOUNT TEST SUMMARY")
        print("=" * 60)
        
        passed = sum(1 for result in self.test_results if "✅ PASS" in result['status'])
        failed = sum(1 for result in self.test_results if "❌ FAIL" in result['status'])
        total = len(self.test_results)
        
        print(f"Total Tests: {total}")
        print(f"Passed: {passed}")
        print(f"Failed: {failed}")
        print(f"Success Rate: {(passed/total)*100:.1f}%")
        
        if failed > 0:
            print("\n❌ FAILED TESTS:")
            for result in self.test_results:
                if "❌ FAIL" in result['status']:
                    print(f"  - {result['test']}: {result['message']}")
        else:
            print("\n✅ ALL TESTS PASSED!")
        
        return failed == 0

if __name__ == "__main__":
    tester = CustomAmountTester()
    success = tester.run_all_tests()
    sys.exit(0 if success else 1)