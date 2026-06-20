import threading
import time
import random
import string
from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import routeros_api

# === CONFIGURAÇÕES GERAIS ===
TELEGRAM_TOKEN = '8955548977:AAF8wFeZFNH2ogqABfikJcaxHONZc4Oldos'
ADMIN_CHAT_ID = '8748799831'

MK_IP = '10.100.10.1' 
MK_USER = 'bot'    
MK_PASS = 'Cleuven2106.'   

app = Flask(__name__)
CORS(app)
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# Armazena os dados em memória
solicitacoes = {}

def gerar_senha(tamanho=6):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=tamanho))

# === ROTAS DA API ===

@app.route('/solicitar', methods=['POST'])
def solicitar():
    dados = request.json
    mac = dados.get('mac')
    ip = dados.get('ip')

    if not mac:
        return jsonify({"erro": "MAC ausente"}), 400

    solicitacoes[mac] = {"status": "pendente", "user": "", "password": "", "ip": ip}

    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("⏱ 10 Min", callback_data=f"aceitar_10m_{mac}"),
        InlineKeyboardButton("⏱ 30 Min", callback_data=f"aceitar_30m_{mac}")
    )
    markup.row(
        InlineKeyboardButton("⏳ 1 Hora", callback_data=f"aceitar_1h_{mac}"),
        InlineKeyboardButton("⏳ 5 Horas", callback_data=f"aceitar_5h_{mac}")
    )
    markup.row(
        InlineKeyboardButton("♾️ Ilimitado", callback_data=f"aceitar_ilim_{mac}"),
        InlineKeyboardButton("❌ Recusar", callback_data=f"recusar_{mac}")
    )
    
    mensagem = f"🔔 *NOVA SOLICITAÇÃO DE ACESSO*\n\n*IP do Cliente:* {ip}\n*MAC:* {mac}\n\nEscolha o tempo de liberação:"
    bot.send_message(ADMIN_CHAT_ID, mensagem, parse_mode="Markdown", reply_markup=markup)

    return jsonify({"message": "Solicitação enviada"}), 200

@app.route('/status', methods=['GET'])
def status():
    mac = request.args.get('mac')
    if mac in solicitacoes:
        return jsonify(solicitacoes[mac]), 200
    return jsonify({"status": "nao_encontrado"}), 404

# === PAINEL DE GERÊNCIA WEB ===

HTML_ADMIN = """
<!DOCTYPE html>
<html lang="pt-br">
<head>
    <meta charset="UTF-8">
    <title>Gerência Nuuvsen</title>
    <meta http-equiv="refresh" content="10">
    <style>
        body { font-family: Arial, sans-serif; background-color: #f4f7f6; padding: 20px; color: #333; }
        .container { max-width: 800px; margin: auto; background: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 4px 8px rgba(0,0,0,0.1); }
        h2 { text-align: center; color: #3b82f6; }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; }
        th, td { padding: 12px; border: 1px solid #ddd; text-align: center; }
        th { background-color: #3b82f6; color: white; }
        .pendente { color: #f59e0b; font-weight: bold; }
        .aprovado { color: #10b981; font-weight: bold; }
        .recusado, .desconectado { color: #ef4444; font-weight: bold; }
    </style>
</head>
<body>
    <div class="container">
        <h2>Painel de Gerência - Nuuvsen Hotspot</h2>
        <p style="text-align:center; font-size: 12px; color: #777;">Atualização automática a cada 10 segundos</p>
        <table>
            <tr>
                <th>MAC do Cliente</th>
                <th>Usuário Gerado</th>
                <th>Status</th>
            </tr>
            {% for mac, dados in solicitacoes.items() %}
            <tr>
                <td>{{ mac }}</td>
                <td>{{ dados.user or '-' }}</td>
                <td class="{{ dados.status }}">{{ dados.status | upper }}</td>
            </tr>
            {% else %}
            <tr><td colspan="3">Nenhuma solicitação registrada ainda.</td></tr>
            {% endfor %}
        </table>
    </div>
</body>
</html>
"""

@app.route('/admin')
def admin():
    return render_template_string(HTML_ADMIN, solicitacoes=solicitacoes)

# === AÇÕES DO TELEGRAM ===

@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    acao, mac = call.data.rsplit('_', 1)

    if acao.startswith("aceitar"):
        if "10m" in acao: tempo = "00:10:00"; txt_tempo = "10 Minutos"
        elif "30m" in acao: tempo = "00:30:00"; txt_tempo = "30 Minutos"
        elif "1h" in acao: tempo = "01:00:00"; txt_tempo = "1 Hora"
        elif "5h" in acao: tempo = "05:00:00"; txt_tempo = "5 Horas"
        else: tempo = "ilimitado"; txt_tempo = "Tempo Ilimitado"
        
        usuario_gerado = f"visitante_{random.randint(1000, 9999)}"
        senha_gerada = gerar_senha()

        try:
            conexao = routeros_api.RouterOsApiPool(MK_IP, username=MK_USER, password=MK_PASS, plaintext_login=True)
            api = conexao.get_api()
            
            parametros_mk = {'name': usuario_gerado, 'password': senha_gerada, 'mac-address': mac, 'profile': 'default'}
            if tempo != "ilimitado":
                parametros_mk['limit-uptime'] = tempo

            api.get_resource('/ip/hotspot/user').add(**parametros_mk)
            conexao.disconnect()

            if mac in solicitacoes:
                solicitacoes[mac].update({"status": "aprovado", "user": usuario_gerado, "password": senha_gerada})
            else:
                solicitacoes[mac] = {"status": "aprovado", "user": usuario_gerado, "password": senha_gerada}
            
            # Adiciona o botão de desconectar após aprovar
            markup_desc = InlineKeyboardMarkup()
            markup_desc.add(InlineKeyboardButton("🛑 Desconectar Usuário", callback_data=f"desconectar_{mac}"))

            bot.edit_message_text(f"✅ *Acesso Aprovado!*\n\n*Tempo Liberado:* {txt_tempo}\n*Usuário:* {usuario_gerado}\n*MAC:* {mac}", 
                                  chat_id=call.message.chat.id, 
                                  message_id=call.message.message_id, 
                                  parse_mode="Markdown", reply_markup=markup_desc)

        except Exception as e:
            bot.send_message(call.message.chat.id, f"❌ Erro ao criar usuário no MikroTik: {e}")

    elif acao == "recusar":
        if mac in solicitacoes:
            solicitacoes[mac]["status"] = "recusado"
        bot.edit_message_text(f"🚫 *Acesso Recusado*\n\n*MAC:* {mac}", 
                              chat_id=call.message.chat.id, 
                              message_id=call.message.message_id, 
                              parse_mode="Markdown")

    elif acao == "desconectar":
        try:
            conexao = routeros_api.RouterOsApiPool(MK_IP, username=MK_USER, password=MK_PASS, plaintext_login=True)
            api = conexao.get_api()
            
            # Recupera o usuário que foi criado para este MAC
            usuario_gerado = solicitacoes.get(mac, {}).get("user")

            if usuario_gerado:
                # 1. Remove da lista de usuários permitidos
                users = api.get_resource('/ip/hotspot/user').get(name=usuario_gerado)
                for u in users:
                    api.get_resource('/ip/hotspot/user').remove(id=u['id'])
                
                # 2. Derruba a conexão ativa instantaneamente
                actives = api.get_resource('/ip/hotspot/active').get(user=usuario_gerado)
                for a in actives:
                    api.get_resource('/ip/hotspot/active').remove(id=a['id'])

            conexao.disconnect()

            if mac in solicitacoes:
                solicitacoes[mac]["status"] = "desconectado"

            bot.edit_message_text(f"🛑 *Usuário Desconectado!*\n\nA conexão de {usuario_gerado} foi encerrada e o acesso revogado.", 
                                  chat_id=call.message.chat.id, 
                                  message_id=call.message.message_id, 
                                  parse_mode="Markdown")
        except Exception as e:
            bot.send_message(call.message.chat.id, f"❌ Erro ao desconectar no MikroTik: {e}")

def iniciar_bot():
    bot.polling(none_stop=True)

if __name__ == '__main__':
    threading.Thread(target=iniciar_bot, daemon=True).start()
    app.run(host='0.0.0.0', port=5000)
