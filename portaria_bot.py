import threading
import random
import string
from flask import Flask, request, jsonify
from flask_cors import CORS
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import routeros_api

# =========================================================
# 1. CONFIGURAÇÕES (PREENCHA COM OS SEUS DADOS)
# =========================================================
TELEGRAM_BOT_TOKEN = "8955548977:AAF8wFeZFNH2ogqABfikJcaxHONZc4Oldos"
TELEGRAM_CHAT_ID = "8748799831"

MIKROTIK_IP = "10.100.10.1"
MIKROTIK_USER = "bot"
MIKROTIK_PASS = "Cleuven2106."

# =========================================================

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
app = Flask(__name__)
CORS(app)

solicitacoes = {}

def gerar_senha(tamanho=6):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=tamanho))

# =========================================================
# ROTAS DO HOTSPOT (TELAS DE LOGIN)
# =========================================================
@app.route('/solicitar', methods=['POST'])
def solicitar_acesso():
    dados = request.json
    mac = dados.get('mac')
    ip = dados.get('ip')

    if not mac:
        return jsonify({"erro": "MAC não fornecido"}), 400

    solicitacoes[mac] = {'status': 'pendente', 'user': '', 'password': ''}

    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("10 Min", callback_data=f"acc_10m_{mac}"),
               InlineKeyboardButton("30 Min", callback_data=f"acc_30m_{mac}"))
    markup.row(InlineKeyboardButton("1 Hora", callback_data=f"acc_1h_{mac}"),
               InlineKeyboardButton("5 Horas", callback_data=f"acc_5h_{mac}"))
    markup.row(InlineKeyboardButton("Sem Limite", callback_data=f"acc_unl_{mac}"))
    markup.row(InlineKeyboardButton("❌ Recusar", callback_data=f"rej_{mac}"))

    mensagem = f"🔌 *Nova Solicitação de Visitante*\n\n*MAC:* `{mac}`\n*IP:* `{ip}`\n\nEscolha o tempo de acesso:"
    
    try:
        bot.send_message(TELEGRAM_CHAT_ID, mensagem, reply_markup=markup, parse_mode='Markdown')
    except Exception as e:
        print(f"Erro Telegram: {e}")

    return jsonify({"mensagem": "Solicitação enviada"}), 200

@app.route('/status', methods=['GET'])
def verificar_status():
    mac = request.args.get('mac')
    if mac in solicitacoes:
        return jsonify(solicitacoes[mac]), 200
    return jsonify({"status": "nao_encontrado"}), 404

# =========================================================
# ROTAS DO PAINEL WEB DE MONITORAMENTO (NOVAS)
# =========================================================
@app.route('/api/pendentes', methods=['GET'])
def listar_pendentes():
    # Retorna apenas as solicitações que ainda não foram aceites
    pendentes = {mac: dados for mac, dados in solicitacoes.items() if dados['status'] == 'pendente'}
    return jsonify(pendentes), 200

@app.route('/api/ativos', methods=['GET'])
def listar_ativos():
    try:
        connection = routeros_api.RouterOsApiPool(MIKROTIK_IP, username=MIKROTIK_USER, password=MIKROTIK_PASS, plaintext_login=True)
        api = connection.get_api()
        ativos = api.get_resource('/ip/hotspot/active').get()
        
        dados = []
        for user in ativos:
            # O MikroTik envia os dados em bytes. Vamos converter para MegaBytes (MB)
            bytes_in = int(user.get('bytes-in', 0)) / (1024 * 1024)
            bytes_out = int(user.get('bytes-out', 0)) / (1024 * 1024)
            
            dados.append({
                'user': user.get('user', 'Desconhecido'),
                'mac': user.get('mac-address', 'N/A'),
                'ip': user.get('address', 'N/A'),
                'uptime': user.get('uptime', '0s'),
                'download_mb': f"{bytes_out:.1f} MB", 
                'upload_mb': f"{bytes_in:.1f} MB"
            })
        
        connection.disconnect()
        return jsonify(dados), 200
    except Exception as e:
        return jsonify({"erro": str(e)}), 500

@app.route('/api/desconectar', methods=['POST'])
def desconectar_usuario():
    mac = request.json.get('mac')
    try:
        connection = routeros_api.RouterOsApiPool(MIKROTIK_IP, username=MIKROTIK_USER, password=MIKROTIK_PASS, plaintext_login=True)
        api = connection.get_api()
        recurso_ativo = api.get_resource('/ip/hotspot/active')
        ativos = recurso_ativo.get()
        
        for user in ativos:
            if user.get('mac-address') == mac:
                recurso_ativo.remove(id=user.get('id'))
                break
                
        connection.disconnect()
        return jsonify({"mensagem": "Desconectado"}), 200
    except Exception as e:
        return jsonify({"erro": str(e)}), 500

@app.route('/api/aprovar', methods=['POST'])
def aprovar_painel():
    dados = request.json
    mac = dados.get('mac')
    tempo = dados.get('tempo')
    
    if mac in solicitacoes and solicitacoes[mac]['status'] == 'pendente':
        limites = {"10m": "00:10:00", "30m": "00:30:00", "1h": "01:00:00", "5h": "05:00:00", "unl": None}
        limite_mikrotik = limites.get(tempo)
        
        usuario_hotspot = f"vis_{mac[-5:].replace(':', '')}"
        senha_hotspot = gerar_senha()

        try:
            connection = routeros_api.RouterOsApiPool(MIKROTIK_IP, username=MIKROTIK_USER, password=MIKROTIK_PASS, plaintext_login=True)
            api = connection.get_api()
            
            user_data = {
                'name': usuario_hotspot,
                'password': senha_hotspot,
                'mac-address': mac,
                'profile': 'convidado'
            }
            if limite_mikrotik:
                user_data['limit-uptime'] = limite_mikrotik

            api.get_resource('/ip/hotspot/user').add(**user_data)
            connection.disconnect()

            solicitacoes[mac] = {'status': 'aprovado', 'user': usuario_hotspot, 'password': senha_hotspot}
            
            # Avisa no Telegram que foi aprovado pelo Painel
            bot.send_message(TELEGRAM_CHAT_ID, f"✅ O MAC `{mac}` foi aprovado diretamente pelo Painel Web!", parse_mode='Markdown')
            
            return jsonify({"mensagem": "Aprovado"}), 200
        except Exception as e:
            return jsonify({"erro": str(e)}), 500
            
    return jsonify({"erro": "Não encontrado"}), 404

@app.route('/api/recusar', methods=['POST'])
def recusar_painel():
    mac = request.json.get('mac')
    if mac in solicitacoes:
        solicitacoes[mac] = {'status': 'recusado'}
        bot.send_message(TELEGRAM_CHAT_ID, f"❌ O MAC `{mac}` foi recusado diretamente pelo Painel Web!", parse_mode='Markdown')
        return jsonify({"mensagem": "Recusado"}), 200
    return jsonify({"erro": "Não encontrado"}), 404

# =========================================================
# LÓGICA DO TELEGRAM BOT (BOTÕES)
# =========================================================
@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    acao = call.data.split('_')[0]
    tempo = call.data.split('_')[1]
    mac = call.data.split('_')[2]

    if acao == "rej":
        solicitacoes[mac] = {'status': 'recusado'}
        bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, 
                              text=f"❌ Acesso recusado para o MAC:\n`{mac}`", parse_mode='Markdown')
        return

    if acao == "acc":
        limites = {"10m": "00:10:00", "30m": "00:30:00", "1h": "01:00:00", "5h": "05:00:00", "unl": None}
        limite_mikrotik = limites.get(tempo)
        
        usuario_hotspot = f"vis_{mac[-5:].replace(':', '')}"
        senha_hotspot = gerar_senha()

        try:
            connection = routeros_api.RouterOsApiPool(MIKROTIK_IP, username=MIKROTIK_USER, password=MIKROTIK_PASS, plaintext_login=True)
            api = connection.get_api()
            
            user_data = {
                'name': usuario_hotspot,
                'password': senha_hotspot,
                'mac-address': mac,
                'profile': 'convidado'
            }
            if limite_mikrotik:
                user_data['limit-uptime'] = limite_mikrotik

            api.get_resource('/ip/hotspot/user').add(**user_data)
            connection.disconnect()

            solicitacoes[mac] = {'status': 'aprovado', 'user': usuario_hotspot, 'password': senha_hotspot}
            
            text_tempo = "Sem Limite" if tempo == "unl" else tempo.replace("m", " Min").replace("h", " Hora(s)")
            bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, 
                                  text=f"✅ *Acesso Aprovado!*\n\n*MAC:* `{mac}`\n*Tempo:* {text_tempo}\n*Utilizador:* `{usuario_hotspot}`", parse_mode='Markdown')
        except Exception as e:
            bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, 
                                  text=f"⚠️ Erro ao ligar ao MikroTik:\n`{e}`", parse_mode='Markdown')

def iniciar_bot():
    bot.polling(none_stop=True)

if __name__ == '__main__':
    threading.Thread(target=iniciar_bot, daemon=True).start()
    print("API de Portaria e Painel iniciada na porta 5000...")
    app.run(host='0.0.0.0', port=5000)