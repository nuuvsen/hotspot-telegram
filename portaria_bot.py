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

# === MOTOR DE AÇÕES (Usado pelo Telegram e pelo Painel Web) ===

def executar_acao(acao, mac):
    if mac not in solicitacoes:
        return False, "nao_encontrado", "Solicitação não encontrada."

    if acao.startswith("aceitar"):
        if "10m" in acao: tempo = "00:10:00"; txt_tempo = "10 Minutos"
        elif "30m" in acao: tempo = "00:30:00"; txt_tempo = "30 Minutos"
        elif "1h" in acao: tempo = "01:00:00"; txt_tempo = "1 Hora"
        elif "5h" in acao: tempo = "05:00:00"; txt_tempo = "5 Horas"
        else: tempo = "ilimitado"; txt_tempo = "Tempo Ilimitado"
        
        # Cria um usuário fixo baseado no MAC (Evita duplicações no Mikrotik)
        usuario_gerado = f"vis_{mac.replace(':', '')}"
        senha_gerada = gerar_senha()

        try:
            conexao = routeros_api.RouterOsApiPool(MK_IP, username=MK_USER, password=MK_PASS, plaintext_login=True)
            api = conexao.get_api()
            recurso_user = api.get_resource('/ip/hotspot/user')
            
            # Verifica se o usuário já existe
            usuarios_existentes = recurso_user.get(name=usuario_gerado)
            
            # ATENÇÃO: O profile 'convidado' deve existir no MikroTik!
            parametros_mk = {
                'password': senha_gerada, 
                'mac-address': mac, 
                'profile': 'convidado',
                'disabled': 'false' # Reativa caso estivesse desconectado
            }
            
            if tempo != "ilimitado":
                parametros_mk['limit-uptime'] = tempo
            else:
                parametros_mk['limit-uptime'] = '0s' # 0s limpa o limite de tempo no mikrotik

            if usuarios_existentes:
                # Se existir, APENAS ATUALIZA
                parametros_mk['id'] = usuarios_existentes[0]['id']
                recurso_user.set(**parametros_mk)
            else:
                # Se não existir, CRIA UM NOVO
                parametros_mk['name'] = usuario_gerado
                recurso_user.add(**parametros_mk)
            
            # Se o usuário estava online na tela de bloqueio, derruba para logar com o novo tempo
            actives = api.get_resource('/ip/hotspot/active').get(mac_address=mac)
            for a in actives:
                api.get_resource('/ip/hotspot/active').remove(id=a['id'])

            conexao.disconnect()

            solicitacoes[mac].update({"status": "aprovado", "user": usuario_gerado, "password": senha_gerada})
            msg = f"✅ *Acesso Aprovado!*\n\n*Tempo:* {txt_tempo}\n*Usuário:* {usuario_gerado}\n*MAC:* {mac}"
            return True, "aprovado", msg

        except Exception as e:
            return False, "erro", f"Erro no MikroTik: {e}"

    elif acao == "recusar":
        solicitacoes[mac]["status"] = "recusado"
        return True, "recusado", f"🚫 *Acesso Recusado*\n\n*MAC:* {mac}"

    elif acao == "desconectar":
        usuario_gerado = f"vis_{mac.replace(':', '')}"
        try:
            conexao = routeros_api.RouterOsApiPool(MK_IP, username=MK_USER, password=MK_PASS, plaintext_login=True)
            api = conexao.get_api()
            
            # 1. Desativa o usuário para não logar de novo sozinho
            users = api.get_resource('/ip/hotspot/user').get(name=usuario_gerado)
            for u in users:
                api.get_resource('/ip/hotspot/user').set(id=u['id'], disabled='true')
            
            # 2. Derruba a conexão ativa na hora
            actives = api.get_resource('/ip/hotspot/active').get(user=usuario_gerado)
            for a in actives:
                api.get_resource('/ip/hotspot/active').remove(id=a['id'])

            conexao.disconnect()
            solicitacoes[mac]["status"] = "desconectado"
            return True, "desconectado", f"🛑 *Usuário Desconectado!*\n\nA conexão de {usuario_gerado} foi encerrada."
            
        except Exception as e:
            return False, "erro", f"Erro ao desconectar no MikroTik: {e}"


# === ROTAS DA API HOTSPOT ===

@app.route('/solicitar', methods=['POST'])
def solicitar():
    dados = request.json
    mac = dados.get('mac')
    ip = dados.get('ip')

    if not mac: return jsonify({"erro": "MAC ausente"}), 400

    solicitacoes[mac] = {"status": "pendente", "user": "", "password": "", "ip": ip}

    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("⏱ 10 Min", callback_data=f"aceitar_10m_{mac}"), InlineKeyboardButton("⏱ 30 Min", callback_data=f"aceitar_30m_{mac}"))
    markup.row(InlineKeyboardButton("⏳ 1 Hora", callback_data=f"aceitar_1h_{mac}"), InlineKeyboardButton("⏳ 5 Horas", callback_data=f"aceitar_5h_{mac}"))
    markup.row(InlineKeyboardButton("♾️ Ilimitado", callback_data=f"aceitar_ilim_{mac}"), InlineKeyboardButton("❌ Recusar", callback_data=f"recusar_{mac}"))
    
    mensagem = f"🔔 *NOVA SOLICITAÇÃO DE ACESSO*\n\n*IP:* {ip}\n*MAC:* {mac}\n\nEscolha o tempo de liberação:"
    bot.send_message(ADMIN_CHAT_ID, mensagem, parse_mode="Markdown", reply_markup=markup)

    return jsonify({"message": "Solicitação enviada"}), 200

@app.route('/status', methods=['GET'])
def status():
    mac = request.args.get('mac')
    if mac in solicitacoes: return jsonify(solicitacoes[mac]), 200
    return jsonify({"status": "nao_encontrado"}), 404


# === PAINEL DE GERÊNCIA WEB ===

HTML_ADMIN = """
<!DOCTYPE html>
<html lang="pt-br">
<head>
    <meta charset="UTF-8">
    <title>Gerência Nuuvsen</title>
    <style>
        body { font-family: Arial, sans-serif; background-color: #f4f7f6; padding: 20px; color: #333; }
        .container { max-width: 900px; margin: auto; background: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 4px 8px rgba(0,0,0,0.1); }
        h2 { text-align: center; color: #3b82f6; }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; font-size: 14px; }
        th, td { padding: 12px; border: 1px solid #ddd; text-align: center; vertical-align: middle; }
        th { background-color: #3b82f6; color: white; }
        .pendente { color: #f59e0b; font-weight: bold; }
        .aprovado { color: #10b981; font-weight: bold; }
        .recusado, .desconectado { color: #ef4444; font-weight: bold; }
        
        .btn { padding: 6px 10px; margin: 2px; border: none; border-radius: 4px; cursor: pointer; color: white; font-weight: bold; font-size: 12px; }
        .btn-green { background-color: #10b981; }
        .btn-green:hover { background-color: #059669; }
        .btn-blue { background-color: #3b82f6; }
        .btn-blue:hover { background-color: #2563eb; }
        .btn-red { background-color: #ef4444; }
        .btn-red:hover { background-color: #dc2626; }
        .acoes { display: flex; flex-wrap: wrap; justify-content: center; gap: 4px; }
    </style>
</head>
<body>
    <div class="container">
        <h2>Painel de Gerência - Nuuvsen Hotspot</h2>
        <button onclick="location.reload()" style="padding: 8px; margin-bottom: 10px; cursor:pointer;">🔄 Atualizar Tela</button>
        <table>
            <tr>
                <th>MAC do Cliente</th>
                <th>IP</th>
                <th>Status</th>
                <th>Ações Rápida</th>
            </tr>
            {% for mac, dados in solicitacoes.items() %}
            <tr>
                <td>{{ mac }}</td>
                <td>{{ dados.ip or '-' }}</td>
                <td class="{{ dados.status }}">{{ dados.status | upper }}</td>
                <td>
                    {% if dados.status == 'pendente' or dados.status == 'desconectado' or dados.status == 'recusado' %}
                        <div class="acoes">
                            <button class="btn btn-green" onclick="fazerAcao('aceitar_10m', '{{ mac }}')">10m</button>
                            <button class="btn btn-green" onclick="fazerAcao('aceitar_30m', '{{ mac }}')">30m</button>
                            <button class="btn btn-green" onclick="fazerAcao('aceitar_1h', '{{ mac }}')">1h</button>
                            <button class="btn btn-green" onclick="fazerAcao('aceitar_5h', '{{ mac }}')">5h</button>
                            <button class="btn btn-blue" onclick="fazerAcao('aceitar_ilim', '{{ mac }}')">Ilim.</button>
                            <button class="btn btn-red" onclick="fazerAcao('recusar', '{{ mac }}')">Recusar</button>
                        </div>
                    {% elif dados.status == 'aprovado' %}
                        <div class="acoes">
                            <button class="btn btn-red" onclick="fazerAcao('desconectar', '{{ mac }}')">🛑 Desconectar</button>
                        </div>
                    {% endif %}
                </td>
            </tr>
            {% else %}
            <tr><td colspan="4">Nenhuma solicitação registrada na memória.</td></tr>
            {% endfor %}
        </table>
    </div>

    <script>
        function fazerAcao(acao, mac) {
            fetch('/admin/acao', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ acao: acao, mac: mac })
            })
            .then(res => res.json())
            .then(data => {
                if(data.sucesso) {
                    location.reload();
                } else {
                    alert("Erro: " + data.mensagem);
                }
            });
        }
    </script>
</body>
</html>
"""

@app.route('/admin')
def admin():
    return render_template_string(HTML_ADMIN, solicitacoes=solicitacoes)

@app.route('/admin/acao', methods=['POST'])
def admin_acao():
    dados = request.json
    sucesso, status_result, msg = executar_acao(dados.get('acao'), dados.get('mac'))
    
    # Se fez a ação pelo painel, notifica no Telegram para manter o histórico
    if sucesso:
        bot.send_message(ADMIN_CHAT_ID, f"💻 *Ação via Painel Web:*\n{msg}", parse_mode="Markdown")

    return jsonify({"sucesso": sucesso, "mensagem": msg})


# === AÇÕES DO TELEGRAM ===

@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    acao, mac = call.data.rsplit('_', 1)
    
    sucesso, status_result, msg = executar_acao(acao, mac)

    if sucesso:
        if status_result == "aprovado":
            markup_desc = InlineKeyboardMarkup()
            markup_desc.add(InlineKeyboardButton("🛑 Desconectar Usuário", callback_data=f"desconectar_{mac}"))
            bot.edit_message_text(msg, chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=markup_desc)
        else:
            bot.edit_message_text(msg, chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown")
    else:
        bot.send_message(call.message.chat.id, msg)

def iniciar_bot():
    bot.polling(none_stop=True)

if __name__ == '__main__':
    threading.Thread(target=iniciar_bot, daemon=True).start()
    app.run(host='0.0.0.0', port=5000)
