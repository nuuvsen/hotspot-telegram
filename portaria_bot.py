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

solicitacoes = {}

def gerar_senha(tamanho=6):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=tamanho))

# === THREAD DE MONITORAMENTO MIKROTIK ===
def monitorar_conexoes():
    while True:
        try:
            aprovados = {mac: dados for mac, dados in solicitacoes.items() if dados.get("status") == "aprovado"}
            
            if aprovados:
                conexao = routeros_api.RouterOsApiPool(MK_IP, username=MK_USER, password=MK_PASS, plaintext_login=True)
                api = conexao.get_api()
                
                usuarios_ativos = api.get_resource('/ip/hotspot/active').get()
                ativos_dict = {u.get('user'): u for u in usuarios_ativos}

                for mac, dados in aprovados.items():
                    usuario = dados.get("user")
                    
                    if usuario in ativos_dict:
                        dados["is_online"] = True
                        info_ativo = ativos_dict[usuario]
                        dados["time_left"] = info_ativo.get("session-time-left", "Ilimitado")
                    else:
                        dados["is_online"] = False
                        dados["time_left"] = "Offline"
                
                conexao.disconnect()
        except Exception as e:
            print(f"Erro no monitoramento: {e}")
        
        time.sleep(10)

# === MOTOR DE AÇÕES ===
def executar_acao(acao, mac):
    # Padroniza o MAC para maiúsculo para garantir a busca correta no MK
    mac = mac.upper() 
    
    if mac not in solicitacoes:
        return False, "nao_encontrado", "Solicitação não encontrada."

    nome_cliente = solicitacoes[mac].get('nome', 'Visitante')

    if acao.startswith("aceitar"):
        if "10m" in acao: txt_tempo = "10 Minutos"; perfil = "10m"
        elif "30m" in acao: txt_tempo = "30 Minutos"; perfil = "30m"
        elif "1h" in acao: txt_tempo = "1 Hora"; perfil = "1h"
        elif "5h" in acao: txt_tempo = "5 Horas"; perfil = "5h"
        else: txt_tempo = "Tempo Ilimitado"; perfil = "ilimitado"
        
        usuario_gerado = f"vis_{mac.replace(':', '')}"
        senha_gerada = gerar_senha()

        try:
            conexao = routeros_api.RouterOsApiPool(MK_IP, username=MK_USER, password=MK_PASS, plaintext_login=True)
            api = conexao.get_api()
            recurso_user = api.get_resource('/ip/hotspot/user')
            recurso_host = api.get_resource('/ip/hotspot/host')
            
            # 1. CONFIGURA O USUÁRIO (Sem forçar o 'address'. O MikroTik dará o IP da Pool do Perfil)
            parametros_mk = {
                'password': senha_gerada, 
                'mac-address': mac, 
                'profile': perfil,
                'disabled': 'false',
                'comment': f"Nome: {nome_cliente}"
            }
            
            usuarios_existentes = recurso_user.get(name=usuario_gerado)
            
            if usuarios_existentes:
                parametros_mk['id'] = usuarios_existentes[0]['id']
                recurso_user.set(**parametros_mk)
            else:
                parametros_mk['name'] = usuario_gerado
                recurso_user.add(**parametros_mk)
            
            # 2. DERRUBA TUDO PARA FORÇAR A TROCA DO IP E DA POOL NO APARELHO
            
            # A) Derruba o usuário do Active (se existir)
            actives = api.get_resource('/ip/hotspot/active').get()
            for a in actives:
                if a.get('mac-address', '').upper() == mac:
                    api.get_resource('/ip/hotspot/active').remove(id=a['id'])

            # B) Derruba o dispositivo da aba Host
            todos_hosts = recurso_host.get()
            for h in todos_hosts:
                if h.get('mac-address', '').upper() == mac:
                    try:
                        recurso_host.remove(id=h['id'])
                    except: pass
            
            # C) Apaga o registro no DHCP Server (Lease da pool de login)
            # Isso força o aparelho a pedir um IP novo e ser encaixado na pool do perfil
            try:
                recurso_dhcp = api.get_resource('/ip/dhcp-server/lease')
                leases = recurso_dhcp.get()
                for l in leases:
                    if l.get('mac-address', '').upper() == mac:
                        recurso_dhcp.remove(id=l['id'])
            except Exception as e:
                pass 

            conexao.disconnect()

            solicitacoes[mac].update({"status": "aprovado", "user": usuario_gerado, "password": senha_gerada, "is_online": False, "time_left": txt_tempo})
            
            msg = f"✅ *Acesso Aprovado!*\n\n*Nome:* {nome_cliente}\n*Tempo:* {txt_tempo}\n*Perfil:* {perfil}\n*Ação:* Forçando renovação de IP via DHCP\n*Usuário:* {usuario_gerado}\n*MAC:* {mac}"
            return True, "aprovado", msg

        except Exception as e:
            return False, "erro", f"Erro no MikroTik: {e}"

    elif acao == "recusar":
        solicitacoes[mac]["status"] = "recusado"
        return True, "recusado", f"🚫 *Acesso Recusado*\n\n*Nome:* {nome_cliente}\n*MAC:* {mac}"

    elif acao == "desconectar":
        usuario_gerado = f"vis_{mac.replace(':', '')}"
        mac_cliente = mac.upper()
        try:
            conexao = routeros_api.RouterOsApiPool(MK_IP, username=MK_USER, password=MK_PASS, plaintext_login=True)
            api = conexao.get_api()
            
            users = api.get_resource('/ip/hotspot/user').get(name=usuario_gerado)
            for u in users:
                api.get_resource('/ip/hotspot/user').set(id=u['id'], disabled='true')
            
            actives = api.get_resource('/ip/hotspot/active').get()
            for a in actives:
                if a.get('mac-address', '').upper() == mac_cliente or a.get('user') == usuario_gerado:
                    api.get_resource('/ip/hotspot/active').remove(id=a['id'])

            conexao.disconnect()
            solicitacoes[mac].update({"status": "desconectado", "is_online": False, "time_left": "-"})
            return True, "desconectado", f"🛑 *Usuário Desconectado!*\n\nA conexão de {nome_cliente} ({usuario_gerado}) foi encerrada."
            
        except Exception as e:
            return False, "erro", f"Erro ao desconectar: {e}"

# === ROTAS DA API HOTSPOT E TELEGRAM ===
@app.route('/solicitar', methods=['POST'])
def solicitar():
    dados = request.json
    mac = dados.get('mac', '').upper() 
    ip = dados.get('ip')
    nome = dados.get('nome', 'Visitante Sem Nome').strip()
    if not nome: nome = 'Visitante Sem Nome'

    if not mac: return jsonify({"erro": "MAC ausente"}), 400

    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("⏱ 10 Min", callback_data=f"aceitar_10m_{mac}"), InlineKeyboardButton("⏱ 30 Min", callback_data=f"aceitar_30m_{mac}"))
    markup.row(InlineKeyboardButton("⏳ 1 Hora", callback_data=f"aceitar_1h_{mac}"), InlineKeyboardButton("⏳ 5 Horas", callback_data=f"aceitar_5h_{mac}"))
    markup.row(InlineKeyboardButton("♾️ Ilimitado", callback_data=f"aceitar_ilim_{mac}"), InlineKeyboardButton("❌ Recusar", callback_data=f"recusar_{mac}"))
    
    mensagem = f"🔔 *NOVA SOLICITAÇÃO DE ACESSO*\n\n*Nome:* {nome}\n*IP Solicitante:* {ip}\n*MAC:* {mac}\n\nEscolha o tempo de liberação:"
    
    msg_enviada = bot.send_message(ADMIN_CHAT_ID, mensagem, parse_mode="Markdown", reply_markup=markup)
    
    solicitacoes[mac] = {
        "status": "pendente", "user": "", "password": "", "ip": ip, "nome": nome, 
        "message_id": msg_enviada.message_id, "is_online": False, "time_left": "-"
    }

    return jsonify({"message": "Solicitação enviada"}), 200

@app.route('/status', methods=['GET'])
def status():
    mac = request.args.get('mac', '').upper()
    if mac in solicitacoes: return jsonify(solicitacoes[mac]), 200
    return jsonify({"status": "nao_encontrado"}), 404

@app.route('/admin/dados', methods=['GET'])
def admin_dados():
    return jsonify(solicitacoes)


# === PAINEL DE GERÊNCIA WEB AVANÇADO ===
HTML_ADMIN = """
<!DOCTYPE html>
<html lang="pt-br">
<head>
    <meta charset="UTF-8">
    <title>Painel Pro - Nuuvsen Hotspot</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * { box-sizing: border-box; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
        body { background-color: #eef2f5; padding: 20px; color: #333; margin: 0; }
        .container { max-width: 1050px; margin: auto; background: #fff; padding: 25px; border-radius: 10px; box-shadow: 0 5px 15px rgba(0,0,0,0.05); }
        h2 { text-align: center; color: #2563eb; margin-top: 0; }
        
        #toast { visibility: hidden; min-width: 250px; background-color: #333; color: #fff; text-align: center; border-radius: 5px; padding: 16px; position: fixed; z-index: 1; right: 20px; top: 20px; box-shadow: 0 4px 8px rgba(0,0,0,0.2); transition: 0.3s; }
        #toast.show { visibility: visible; }
        #toast.success { background-color: #10b981; }
        #toast.error { background-color: #ef4444; }

        .status-badge { padding: 4px 8px; border-radius: 12px; font-size: 12px; font-weight: bold; color: white; display: inline-block; }
        .badge-pendente { background-color: #f59e0b; }
        .badge-aprovado { background-color: #10b981; }
        .badge-recusado, .badge-desconectado { background-color: #ef4444; }

        table { width: 100%; border-collapse: collapse; margin-top: 15px; font-size: 14px; }
        th, td { padding: 15px 10px; border-bottom: 1px solid #eee; text-align: center; vertical-align: middle; }
        th { background-color: #f8fafc; color: #64748b; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
        tr:hover { background-color: #f8fafc; }
        
        .btn { padding: 8px 12px; margin: 3px; border: none; border-radius: 6px; cursor: pointer; color: white; font-weight: 600; font-size: 12px; transition: 0.2s; }
        .btn-green { background-color: #10b981; } .btn-green:hover { background-color: #059669; }
        .btn-blue { background-color: #3b82f6; } .btn-blue:hover { background-color: #2563eb; }
        .btn-red { background-color: #ef4444; } .btn-red:hover { background-color: #dc2626; }
        .acoes { display: flex; flex-wrap: wrap; justify-content: center; gap: 5px; }
        
        .live-indicator { display: inline-block; width: 8px; height: 8px; background-color: #10b981; border-radius: 50%; margin-right: 5px; animation: blink 1.5s infinite; }
        @keyframes blink { 0% {opacity: 1;} 50% {opacity: 0.4;} 100% {opacity: 1;} }
        
        .conexao-info { font-size: 12px; margin-top: 5px; font-weight: 500;}
        .online { color: #10b981; }
        .offline { color: #ef4444; }
        .tempo { color: #64748b; font-size: 11px; }
    </style>
</head>
<body>
    <div id="toast">Notificação</div>

    <div class="container">
        <h2>Painel de Gerência Hotspot</h2>
        <div style="text-align: center; margin-bottom: 20px; color: #64748b; font-size: 13px;">
            <span class="live-indicator"></span> Sincronizado em tempo real com MikroTik & Telegram
        </div>
        
        <table id="tabela-solicitacoes">
            <thead>
                <tr>
                    <th>Visitante / MAC</th>
                    <th>IP</th>
                    <th>Status / Conexão</th>
                    <th>Ações Rápidas</th>
                </tr>
            </thead>
            <tbody id="tabela-corpo">
                <tr><td colspan="4">Carregando dados...</td></tr>
            </tbody>
        </table>
    </div>

    <script>
        function showToast(msg, tipo) {
            const toast = document.getElementById("toast");
            toast.className = "show " + tipo;
            toast.innerText = msg;
            setTimeout(() => { toast.className = toast.className.replace("show", ""); }, 3000);
        }

        function fazerAcao(acao, mac) {
            event.target.innerText = "Processando...";
            event.target.style.opacity = "0.5";

            fetch('/admin/acao', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ acao: acao, mac: mac })
            })
            .then(res => res.json())
            .then(data => {
                if(data.sucesso) {
                    showToast("Ação realizada com sucesso!", "success");
                    carregarDados();
                } else {
                    showToast("Erro: " + data.mensagem, "error");
                    carregarDados();
                }
            });
        }

        function carregarDados() {
            fetch('/admin/dados')
            .then(res => res.json())
            .then(data => {
                const tbody = document.getElementById('tabela-corpo');
                tbody.innerHTML = '';
                const macs = Object.keys(data);
                
                if(macs.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="4" style="color:#94a3b8; padding:30px;">Nenhum dispositivo registrado ainda.</td></tr>';
                    return;
                }
                
                macs.forEach(mac => {
                    const req = data[mac];
                    let botoes = '';
                    let statusClass = `badge-${req.status}`;
                    let conexaoHtml = '';
                    
                    if(req.status === 'pendente' || req.status === 'desconectado' || req.status === 'recusado') {
                        botoes = `
                            <button class="btn btn-green" onclick="fazerAcao('aceitar_10m', '${mac}')">10m</button>
                            <button class="btn btn-green" onclick="fazerAcao('aceitar_30m', '${mac}')">30m</button>
                            <button class="btn btn-green" onclick="fazerAcao('aceitar_1h', '${mac}')">1h</button>
                            <button class="btn btn-green" onclick="fazerAcao('aceitar_5h', '${mac}')">5h</button>
                            <button class="btn btn-blue" onclick="fazerAcao('aceitar_ilim', '${mac}')">Ilim.</button>
                            <button class="btn btn-red" onclick="fazerAcao('recusar', '${mac}')">Recusar</button>
                        `;
                    } else if(req.status === 'aprovado') {
                        botoes = `<button class="btn btn-red" onclick="fazerAcao('desconectar', '${mac}')">🛑 Desconectar Usuário</button>`;
                        
                        let stOnline = req.is_online ? '<span class="online">🟢 Online</span>' : '<span class="offline">🔴 Offline</span>';
                        let timeText = req.time_left || '-';
                        conexaoHtml = `<div class="conexao-info">${stOnline}<br><span class="tempo">⏳ Resta: ${timeText}</span></div>`;
                    }
                    
                    let tr = document.createElement('tr');
                    tr.innerHTML = `
                        <td><strong>${req.nome || 'Visitante'}</strong><br><span style="font-size:11px; color:#888;">${mac}</span></td>
                        <td>${req.ip || '-'}</td>
                        <td><span class="status-badge ${statusClass}">${req.status.toUpperCase()}</span>${conexaoHtml}</td>
                        <td><div class="acoes">${botoes}</div></td>
                    `;
                    tbody.appendChild(tr);
                });
            });
        }

        setInterval(carregarDados, 3000);
        window.onload = carregarDados;
    </script>
</body>
</html>
"""

@app.route('/admin')
def admin():
    return render_template_string(HTML_ADMIN)

@app.route('/admin/acao', methods=['POST'])
def admin_acao():
    dados = request.json
    mac = dados.get('mac')
    sucesso, status_result, msg = executar_acao(dados.get('acao'), mac)
    
    if sucesso and mac in solicitacoes:
        msg_id = solicitacoes[mac].get("message_id")
        if msg_id:
            try:
                if status_result == "aprovado":
                    markup_desc = InlineKeyboardMarkup()
                    markup_desc.add(
                        InlineKeyboardButton("🛑 Desconectar Usuário", callback_data=f"desconectar_{mac}"),
                        InlineKeyboardButton("🔄 Atualizar Status", callback_data=f"atualizar_{mac}")
                    )
                    bot.edit_message_text(f"💻 *Aprovado via Painel Web:*\n\n{msg}", chat_id=ADMIN_CHAT_ID, message_id=msg_id, parse_mode="Markdown", reply_markup=markup_desc)
                else:
                    bot.edit_message_text(f"💻 *Ação via Painel Web:*\n\n{msg}", chat_id=ADMIN_CHAT_ID, message_id=msg_id, parse_mode="Markdown")
            except Exception as e:
                pass 

    return jsonify({"sucesso": sucesso, "mensagem": msg})

@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    acao, mac = call.data.rsplit('_', 1)
    
    if acao == "atualizar":
        req = solicitacoes.get(mac)
        if req and req.get("status") == "aprovado":
            st_txt = "🟢 *Online*" if req.get("is_online") else "🔴 *Offline*"
            tempo_txt = req.get("time_left", "N/A")
            
            msg = f"✅ *Acesso Aprovado!*\n\n*Nome:* {req['nome']}\n*Usuário:* {req['user']}\n*MAC:* {mac}\n\n*Status Conexão:* {st_txt}\n*Tempo Restante:* {tempo_txt}"
            
            markup_desc = InlineKeyboardMarkup()
            markup_desc.add(
                InlineKeyboardButton("🛑 Desconectar", callback_data=f"desconectar_{mac}"),
                InlineKeyboardButton("🔄 Atualizar Status", callback_data=f"atualizar_{mac}")
            )
            try:
                bot.edit_message_text(msg, chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=markup_desc)
                bot.answer_callback_query(call.id, "Status atualizado com sucesso!")
            except telebot.apihelper.ApiTelegramException:
                bot.answer_callback_query(call.id, "O status já está atualizado.")
        else:
            bot.answer_callback_query(call.id, "Usuário não está mais aprovado ou não encontrado.")
        return

    sucesso, status_result, msg = executar_acao(acao, mac)

    if sucesso:
        if status_result == "aprovado":
            markup_desc = InlineKeyboardMarkup()
            markup_desc.add(
                InlineKeyboardButton("🛑 Desconectar", callback_data=f"desconectar_{mac}"),
                InlineKeyboardButton("🔄 Atualizar Status", callback_data=f"atualizar_{mac}")
            )
            bot.edit_message_text(msg, chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=markup_desc)
        else:
            bot.edit_message_text(msg, chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown")
    else:
        bot.send_message(call.message.chat.id, msg)

def iniciar_bot():
    bot.polling(none_stop=True)

if __name__ == '__main__':
    threading.Thread(target=iniciar_bot, daemon=True).start()
    threading.Thread(target=monitorar_conexoes, daemon=True).start() 
    app.run(host='0.0.0.0', port=5000)
