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

# Senha para acessar o menu web
WEB_ADMIN_PASS = '123456'

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
            if solicitacoes: 
                conexao = routeros_api.RouterOsApiPool(MK_IP, username=MK_USER, password=MK_PASS, plaintext_login=True)
                api = conexao.get_api()
                
                usuarios_ativos = api.get_resource('/ip/hotspot/active').get()
                ativos_dict = {u.get('user'): u for u in usuarios_ativos}
                
                hosts_ativos = api.get_resource('/ip/hotspot/host').get()
                hosts_macs = [h.get('mac-address', '').upper() for h in hosts_ativos]

                macs_para_desconectar = []

                for mac, dados in solicitacoes.items():
                    usuario = dados.get("user", "")
                    mac_upper = mac.upper()
                    
                    is_in_host = mac_upper in hosts_macs
                    is_in_active = usuario in ativos_dict if usuario else False
                    
                    dados["is_online"] = is_in_host or is_in_active

                    if dados.get("status") == "aprovado":
                        expire_at = dados.get("expire_at")
                        
                        if expire_at is not None:
                            tempo_restante = int(expire_at - time.time())
                            
                            if tempo_restante <= 0:
                                macs_para_desconectar.append(mac)
                                continue
                            
                            m, s = divmod(tempo_restante, 60)
                            h, m = divmod(m, 60)
                            dados["time_left"] = f"{h}h {m}m" if h > 0 else f"{m}m {s}s"
                        else:
                            dados["time_left"] = "Ilimitado"

                        if is_in_active or (expire_at is None and is_in_host):
                            dados["estado_texto"] = "Conectado Autorizado"
                        else:
                            dados["estado_texto"] = "Offline (Tempo Correndo)"
                            
                    else:
                        dados["time_left"] = "-"
                        if is_in_host:
                            dados["estado_texto"] = "Conectado S/ Autorizacao"
                        else:
                            dados["estado_texto"] = "Offline"
                
                conexao.disconnect()

                for m in macs_para_desconectar:
                    executar_acao("desconectar", m)

        except Exception as e:
            print(f"Erro no monitoramento: {e}")
        
        time.sleep(10)

# === MOTOR DE AÇÕES ===
def executar_acao(acao, mac):
    mac = mac.upper() 
    
    if mac not in solicitacoes:
        return False, "nao_encontrado", "Solicitação não encontrada."

    nome_cliente = solicitacoes[mac].get('nome', 'Visitante')

    if acao.startswith("aceitar"):
        segundos = 0
        if "10m" in acao: txt_tempo = "10 Minutos"; perfil = "10m"; segundos = 600
        elif "30m" in acao: txt_tempo = "30 Minutos"; perfil = "30m"; segundos = 1800
        elif "1h" in acao: txt_tempo = "1 Hora"; perfil = "1h"; segundos = 3600
        elif "5h" in acao: txt_tempo = "5 Horas"; perfil = "5h"; segundos = 18000
        else: txt_tempo = "Tempo Ilimitado"; perfil = "ilimitado"; segundos = 0
        
        usuario_gerado = f"vis_{mac.replace(':', '')}"
        senha_gerada = gerar_senha()

        try:
            conexao = routeros_api.RouterOsApiPool(MK_IP, username=MK_USER, password=MK_PASS, plaintext_login=True)
            api = conexao.get_api()
            recurso_user = api.get_resource('/ip/hotspot/user')
            recurso_host = api.get_resource('/ip/hotspot/host')
            recurso_binding = api.get_resource('/ip/hotspot/ip-binding')
            recurso_dhcp = api.get_resource('/ip/dhcp-server/lease')
            
            if perfil == "ilimitado":
                bindings = recurso_binding.get(mac_address=mac)
                if bindings:
                    recurso_binding.set(id=bindings[0]['id'], type='bypassed', comment=f"Ilimitado: {nome_cliente}")
                else:
                    recurso_binding.add(mac_address=mac, type='bypassed', comment=f"Ilimitado: {nome_cliente}")
                
                usuarios_existentes = recurso_user.get(name=usuario_gerado)
                for u in usuarios_existentes: recurso_user.remove(id=u['id'])
                
            else:
                bindings = recurso_binding.get(mac_address=mac)
                for b in bindings: recurso_binding.remove(id=b['id'])

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
            
            actives = api.get_resource('/ip/hotspot/active').get()
            for a in actives:
                if a.get('mac-address', '').upper() == mac:
                    api.get_resource('/ip/hotspot/active').remove(id=a['id'])

            todos_hosts = recurso_host.get()
            for h in todos_hosts:
                if h.get('mac-address', '').upper() == mac:
                    try:
                        recurso_host.remove(id=h['id'])
                    except: pass
            
            try:
                leases = recurso_dhcp.get()
                for l in leases:
                    if l.get('mac-address', '').upper() == mac:
                        recurso_dhcp.remove(id=l['id'])
            except Exception as e:
                pass 

            conexao.disconnect()

            expire_time = time.time() + segundos if segundos > 0 else None

            solicitacoes[mac].update({
                "status": "aprovado", 
                "user": usuario_gerado, 
                "password": senha_gerada, 
                "is_online": False, 
                "estado_texto": "Conectando...", 
                "time_left": txt_tempo,
                "expire_at": expire_time
            })
            
            if perfil == "ilimitado":
                msg = f"✅ *Acesso Aprovado (Bypass Ativado)!*\n\n*Nome:* {nome_cliente}\n*Tempo:* {txt_tempo}\n*Ação:* IP fixado e login automático garantido.\n*MAC:* {mac}"
            else:
                msg = f"✅ *Acesso Aprovado!*\n\n*Nome:* {nome_cliente}\n*Tempo:* {txt_tempo}\n*Perfil:* {perfil}\n*Ação:* DHCP renovado para a Pool do perfil.\n*Usuário:* {usuario_gerado}\n*MAC:* {mac}"
            
            return True, "aprovado", msg

        except Exception as e:
            return False, "erro", f"Erro no MikroTik: {e}"

    elif acao == "recusar":
        solicitacoes[mac].update({"status": "recusado", "estado_texto": "Conectado S/ Autorizacao" if solicitacoes[mac].get("is_online") else "Offline"})
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
            
            recurso_binding = api.get_resource('/ip/hotspot/ip-binding')
            bindings = recurso_binding.get(mac_address=mac_cliente)
            for b in bindings: recurso_binding.remove(id=b['id'])
            
            actives = api.get_resource('/ip/hotspot/active').get()
            for a in actives:
                if a.get('mac-address', '').upper() == mac_cliente or a.get('user') == usuario_gerado:
                    api.get_resource('/ip/hotspot/active').remove(id=a['id'])

            conexao.disconnect()
            solicitacoes[mac].update({"status": "desconectado", "time_left": "-", "estado_texto": "Conectado S/ Autorizacao" if solicitacoes[mac].get("is_online") else "Offline", "expire_at": None})
            return True, "desconectado", f"🛑 *Acesso Encerrado!*\n\nA conexão de {nome_cliente} foi bloqueada e removida da rede."
            
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
    markup.row(InlineKeyboardButton("♾️ Ilimitado (Bypass)", callback_data=f"aceitar_ilim_{mac}"), InlineKeyboardButton("❌ Recusar", callback_data=f"recusar_{mac}"))
    
    mensagem = f"🔔 *NOVA SOLICITAÇÃO DE ACESSO*\n\n*Nome:* {nome}\n*IP Solicitante:* {ip}\n*MAC:* {mac}\n\nEscolha o tempo de liberação:"
    
    msg_enviada = bot.send_message(ADMIN_CHAT_ID, mensagem, parse_mode="Markdown", reply_markup=markup)
    
    solicitacoes[mac] = {
        "status": "pendente", "user": "", "password": "", "ip": ip, "nome": nome, 
        "message_id": msg_enviada.message_id, "is_online": True, "estado_texto": "Conectado S/ Autorizacao", "time_left": "-", "expire_at": None
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

# === ROTA DO MENU DE CONFIGURAÇÃO MIKROTIK ===
@app.route('/admin/config_mk', methods=['POST'])
def config_mk():
    dados = request.json
    senha = dados.get('senha')
    
    if senha != WEB_ADMIN_PASS:
        return jsonify({"sucesso": False, "mensagem": "Senha incorreta. Acesso negado."})
    
    acao = dados.get('acao')
    
    try:
        conexao = routeros_api.RouterOsApiPool(MK_IP, username=MK_USER, password=MK_PASS, plaintext_login=True)
        api = conexao.get_api()
        
        if acao == "criar_perfil":
            nome_perfil = dados.get('nome_perfil')
            session_time = dados.get('session_time')
            rate_limit = dados.get('rate_limit') # Ex: 5M/5M
            
            recurso_profile = api.get_resource('/ip/hotspot/user/profile')
            existente = recurso_profile.get(name=nome_perfil)
            
            parametros = {'session-timeout': session_time, 'rate-limit': rate_limit, 'shared-users': '1'}
            # Filtra parametros vazios
            parametros = {k: v for k, v in parametros.items() if v}
            
            if existente:
                recurso_profile.set(id=existente[0]['id'], **parametros)
                msg = f"Perfil '{nome_perfil}' atualizado na RB!"
            else:
                recurso_profile.add(name=nome_perfil, **parametros)
                msg = f"Perfil '{nome_perfil}' criado na RB!"
                
        conexao.disconnect()
        return jsonify({"sucesso": True, "mensagem": msg})
        
    except Exception as e:
        return jsonify({"sucesso": False, "mensagem": f"Erro na RB: {str(e)}"})


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
        .container { max-width: 1050px; margin: auto; background: #fff; padding: 25px; border-radius: 10px; box-shadow: 0 5px 15px rgba(0,0,0,0.05); position: relative; }
        h2 { text-align: center; color: #2563eb; margin-top: 0; }
        
        #toast { visibility: hidden; min-width: 250px; background-color: #333; color: #fff; text-align: center; border-radius: 5px; padding: 16px; position: fixed; z-index: 100; right: 20px; top: 20px; box-shadow: 0 4px 8px rgba(0,0,0,0.2); transition: 0.3s; }
        #toast.show { visibility: visible; }
        #toast.success { background-color: #10b981; }
        #toast.error { background-color: #ef4444; }

        /* Estilos do Botão Menu e Modal */
        .menu-btn { position: absolute; top: 20px; left: 20px; background: #2563eb; color: white; border: none; padding: 8px 15px; border-radius: 5px; cursor: pointer; font-weight: bold; }
        .menu-btn:hover { background: #1d4ed8; }
        
        .modal-overlay { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); z-index: 50; }
        .modal { position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%); background: white; padding: 25px; border-radius: 10px; width: 90%; max-width: 400px; z-index: 51; box-shadow: 0 10px 25px rgba(0,0,0,0.2); display: none; }
        .modal.active, .modal-overlay.active { display: block; }
        .modal h3 { margin-top: 0; color: #333; border-bottom: 1px solid #eee; padding-bottom: 10px; }
        .form-group { margin-bottom: 15px; }
        .form-group label { display: block; font-size: 13px; color: #64748b; margin-bottom: 5px; }
        .form-group input { width: 100%; padding: 8px; border: 1px solid #cbd5e1; border-radius: 5px; font-size: 14px; }
        .btn-fechar { float: right; background: none; border: none; font-size: 20px; cursor: pointer; color: #999; }

        .tabs { display: flex; justify-content: center; gap: 10px; margin-bottom: 20px; margin-top: 20px; }
        .tab-btn { padding: 10px 20px; border: none; border-radius: 8px; cursor: pointer; font-weight: bold; background-color: #e2e8f0; color: #475569; transition: 0.3s; }
        .tab-btn.active { background-color: #2563eb; color: #fff; }

        .status-badge { padding: 4px 8px; border-radius: 12px; font-size: 12px; font-weight: bold; color: white; display: inline-block; }
        .badge-pendente { background-color: #f59e0b; }
        .badge-aprovado { background-color: #10b981; }
        .badge-recusado, .badge-desconectado { background-color: #ef4444; }

        table { width: 100%; border-collapse: collapse; margin-top: 15px; font-size: 14px; }
        th, td { padding: 15px 10px; border-bottom: 1px solid #eee; text-align: center; vertical-align: middle; }
        th { background-color: #f8fafc; color: #64748b; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
        
        .btn { padding: 8px 12px; margin: 3px; border: none; border-radius: 6px; cursor: pointer; color: white; font-weight: 600; font-size: 12px; transition: 0.2s; }
        .btn-green { background-color: #10b981; } .btn-green:hover { background-color: #059669; }
        .btn-blue { background-color: #3b82f6; } .btn-blue:hover { background-color: #2563eb; }
        .btn-red { background-color: #ef4444; } .btn-red:hover { background-color: #dc2626; }
        .acoes { display: flex; flex-wrap: wrap; justify-content: center; gap: 5px; }
        
        .live-indicator { display: inline-block; width: 8px; height: 8px; background-color: #10b981; border-radius: 50%; margin-right: 5px; animation: blink 1.5s infinite; }
        @keyframes blink { 0% {opacity: 1;} 50% {opacity: 0.4;} 100% {opacity: 1;} }
        
        .conexao-info { font-size: 13px; margin-top: 5px; font-weight: 600; padding: 4px; border-radius: 5px;}
        .estado-verde { color: #10b981; background-color: #ecfdf5;}
        .estado-amarelo { color: #d97706; background-color: #fffbeb;}
        .estado-cinza { color: #64748b; background-color: #f1f5f9;}
        .estado-vermelho { color: #ef4444; background-color: #fef2f2;}
        .tempo { color: #64748b; font-size: 11px; font-weight: normal; display: block; margin-top: 3px;}
    </style>
</head>
<body>
    <div id="toast">Notificação</div>

    <div class="modal-overlay" id="modalOverlay" onclick="fecharModal()"></div>
    <div class="modal" id="modalConfig">
        <button class="btn-fechar" onclick="fecharModal()">&times;</button>
        <h3>⚙️ Configuração na RB</h3>
        
        <div class="form-group">
            <label>Nome do Perfil (Ex: 30m, 1h, Visitante)</label>
            <input type="text" id="mk_nome_perfil" placeholder="Nome do perfil">
        </div>
        <div class="form-group">
            <label>Tempo de Sessão (Ex: 00:30:00 para 30 min, vazio para ilimitado)</label>
            <input type="text" id="mk_session_time" placeholder="00:30:00">
        </div>
        <div class="form-group">
            <label>Limite de Banda / Queue (Ex: 5M/5M, 10M/10M)</label>
            <input type="text" id="mk_rate_limit" placeholder="5M/5M">
        </div>
        <button class="btn btn-blue" style="width: 100%; font-size: 14px; padding: 10px;" onclick="salvarPerfilRB()">Enviar para RB</button>
    </div>

    <div class="container">
        <button class="menu-btn" onclick="abrirMenu()">☰ Menu RB</button>
        
        <h2>Painel de Gerência Hotspot</h2>
        <div style="text-align: center; margin-bottom: 20px; color: #64748b; font-size: 13px;">
            <span class="live-indicator"></span> Monitoramento Inteligente: Calculando tempo no servidor
        </div>

        <div class="tabs">
            <button class="tab-btn active" id="btn-online" onclick="mudarAba('online')">🟢 Na Rede Local (Wi-Fi)</button>
            <button class="tab-btn" id="btn-offline" onclick="mudarAba('offline')">📴 Dispositivos Offline</button>
        </div>
        
        <table id="tabela-solicitacoes">
            <thead>
                <tr>
                    <th>Visitante / MAC</th>
                    <th>IP Base</th>
                    <th>Estado Atual</th>
                    <th>Ações Rápidas</th>
                </tr>
            </thead>
            <tbody id="tabela-corpo">
                <tr><td colspan="4">Carregando dados...</td></tr>
            </tbody>
        </table>
    </div>

    <script>
        let filtroAtual = 'online';
        let senhaAdmin = '';

        function showToast(msg, tipo) {
            const toast = document.getElementById("toast");
            toast.className = "show " + tipo;
            toast.innerText = msg;
            setTimeout(() => { toast.className = toast.className.replace("show", ""); }, 3000);
        }

        /* Lógica do Menu Modal */
        function abrirMenu() {
            if(!senhaAdmin) {
                let senha = prompt("Digite a senha administrativa:");
                if(senha) { senhaAdmin = senha; } else { return; }
            }
            document.getElementById('modalOverlay').classList.add('active');
            document.getElementById('modalConfig').classList.add('active');
        }

        function fecharModal() {
            document.getElementById('modalOverlay').classList.remove('active');
            document.getElementById('modalConfig').classList.remove('active');
        }

        function salvarPerfilRB() {
            const btn = event.target;
            btn.innerText = "Enviando...";
            
            const dados = {
                senha: senhaAdmin,
                acao: 'criar_perfil',
                nome_perfil: document.getElementById('mk_nome_perfil').value,
                session_time: document.getElementById('mk_session_time').value,
                rate_limit: document.getElementById('mk_rate_limit').value
            };

            fetch('/admin/config_mk', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(dados)
            })
            .then(res => res.json())
            .then(data => {
                btn.innerText = "Enviar para RB";
                if(data.sucesso) {
                    showToast(data.mensagem, "success");
                    fecharModal();
                } else {
                    showToast(data.mensagem, "error");
                    if(data.mensagem.includes("Senha")) senhaAdmin = ''; // Reseta se errou
                }
            }).catch(e => {
                btn.innerText = "Enviar para RB";
                showToast("Erro de conexão.", "error");
            });
        }

        /* Lógica Original da Tabela */
        function mudarAba(aba) {
            filtroAtual = aba;
            document.getElementById('btn-online').classList.remove('active');
            document.getElementById('btn-offline').classList.remove('active');
            document.getElementById('btn-' + aba).classList.add('active');
            carregarDados();
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
                
                let temRegistros = false;
                
                macs.forEach(mac => {
                    const req = data[mac];
                    
                    let mostrar = false;
                    if (filtroAtual === 'online') {
                        if (req.is_online === true) mostrar = true;
                    } else if (filtroAtual === 'offline') {
                        if (req.is_online === false) mostrar = true;
                    }

                    if(!mostrar) return;
                    temRegistros = true;

                    let botoes = '';
                    let statusClass = `badge-${req.status}`;
                    let stHtml = '';
                    let timeText = req.time_left || '-';
                    
                    if (req.estado_texto === "Conectado Autorizado") {
                        stHtml = `<div class="conexao-info estado-verde">🟢 Autorizado e Navegando<span class="tempo">⏳ Tempo: ${timeText}</span></div>`;
                    } else if (req.estado_texto === "Conectado S/ Autorizacao") {
                        stHtml = `<div class="conexao-info estado-amarelo">🟡 Conectado (Sem Internet)<span class="tempo">Falta aprovar ou plano acabou</span></div>`;
                    } else if (req.estado_texto === "Offline (Tempo Correndo)") {
                        stHtml = `<div class="conexao-info estado-cinza">📴 Offline (Tempo Correndo)<span class="tempo">⏳ Resta: ${timeText}</span></div>`;
                    } else {
                        stHtml = `<div class="conexao-info estado-vermelho">🔴 Offline e Sem Acesso<span class="tempo">Fora da rede</span></div>`;
                    }

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
                        botoes = `<button class="btn btn-red" onclick="fazerAcao('desconectar', '${mac}')">🛑 Encerrar Acesso</button>`;
                    }
                    
                    let tr = document.createElement('tr');
                    tr.innerHTML = `
                        <td><strong>${req.nome || 'Visitante'}</strong><br><span style="font-size:11px; color:#888;">${mac}</span></td>
                        <td>${req.ip || '-'}</td>
                        <td><span class="status-badge ${statusClass}">${req.status.toUpperCase()}</span>${stHtml}</td>
                        <td><div class="acoes">${botoes}</div></td>
                    `;
                    tbody.appendChild(tr);
                });

                if(!temRegistros) {
                    tbody.innerHTML = '<tr><td colspan="4" style="color:#94a3b8; padding:30px;">Nenhum dispositivo nesta categoria.</td></tr>';
                }
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
                        InlineKeyboardButton("🛑 Encerrar Acesso", callback_data=f"desconectar_{mac}"),
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
        if req:
            st_txt = req.get("estado_texto", "Desconhecido")
            tempo_txt = req.get("time_left", "N/A")
            
            msg = f"🔄 *Status Atualizado*\n\n*Nome:* {req['nome']}\n*MAC:* {mac}\n\n*Status Conexão:* {st_txt}\n*Tempo Restante:* {tempo_txt}"
            
            markup_desc = InlineKeyboardMarkup()
            if req.get("status") == "aprovado":
                markup_desc.add(
                    InlineKeyboardButton("🛑 Encerrar Acesso", callback_data=f"desconectar_{mac}"),
                    InlineKeyboardButton("🔄 Atualizar Status", callback_data=f"atualizar_{mac}")
                )
            try:
                bot.edit_message_text(msg, chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=markup_desc if req.get("status") == "aprovado" else None)
                bot.answer_callback_query(call.id, "Status atualizado com sucesso!")
            except telebot.apihelper.ApiTelegramException:
                bot.answer_callback_query(call.id, "O status já está atualizado.")
        else:
            bot.answer_callback_query(call.id, "Usuário não encontrado.")
        return

    sucesso, status_result, msg = executar_acao(acao, mac)

    if sucesso:
        if status_result == "aprovado":
            markup_desc = InlineKeyboardMarkup()
            markup_desc.add(
                InlineKeyboardButton("🛑 Encerrar Acesso", callback_data=f"desconectar_{mac}"),
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
