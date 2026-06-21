import threading
import time
import random
import string
import re
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
WEB_ADMIN_PASS = 'Cleuven2106.@'

app = Flask(__name__)
CORS(app)
bot = telebot.TeleBot(TELEGRAM_TOKEN)

solicitacoes = {}

# Variável global de configuração do sistema
config_sistema = {
    "gerenciar_tempo_script": True
}

def gerar_senha(tamanho=6):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=tamanho))

# Função para converter o tempo do MikroTik (ex: 1h2m3s) em segundos
def parse_mikrotik_time(t_str):
    if not t_str: return 0
    total_seconds = 0
    matches = re.findall(r'(\d+)([wdhms])', t_str)
    for val, unit in matches:
        val = int(val)
        if unit == 'w': total_seconds += val * 604800
        elif unit == 'd': total_seconds += val * 86400
        elif unit == 'h': total_seconds += val * 3600
        elif unit == 'm': total_seconds += val * 60
        elif unit == 's': total_seconds += val
    return total_seconds

# === THREAD DE MONITORAMENTO MIKROTIK ===
def monitorar_conexoes():
    while True:
        try:
            if solicitacoes: 
                conexao = routeros_api.RouterOsApiPool(MK_IP, username=MK_USER, password=MK_PASS, plaintext_login=True)
                api = conexao.get_api()
                
                hosts_ativos = api.get_resource('/ip/hotspot/host').get()
                # Cria um dicionário dos hosts usando o MAC como chave para busca rápida
                hosts_dict = {h.get('mac-address', '').upper(): h for h in hosts_ativos}

                macs_para_desconectar = []

                for mac, dados in solicitacoes.items():
                    mac_upper = mac.upper()
                    
                    # === VERIFICAÇÃO DE IDLE TIME ===
                    host_info = hosts_dict.get(mac_upper)
                    if host_info:
                        idle_str = host_info.get('idle-time', '0s')
                        idle_sec = parse_mikrotik_time(idle_str)
                        # Se o idle-time for menor que 60 segundos, ele está ONLINE
                        is_online = idle_sec < 60
                    else:
                        # Se sumiu do host, está totalmente offline
                        is_online = False
                    
                    dados["is_online"] = is_online

                    # === GESTÃO DO TEMPO ===
                    if dados.get("status") == "aprovado":
                        expire_at = dados.get("expire_at")
                        
                        if expire_at is not None:
                            tempo_restante = int(expire_at - time.time())
                            
                            if config_sistema["gerenciar_tempo_script"] and tempo_restante <= 0:
                                macs_para_desconectar.append(mac)
                                continue
                            
                            m, s = divmod(tempo_restante, 60)
                            h, m = divmod(m, 60)
                            
                            if tempo_restante <= 0:
                                dados["time_left"] = "Esgotado (Aguardando RB)"
                            else:
                                dados["time_left"] = f"{h}h {m}m" if h > 0 else f"{m}m {s}s"
                        else:
                            dados["time_left"] = "Ilimitado"

                        if is_online:
                            dados["estado_texto"] = "Conectado Autorizado"
                        else:
                            dados["estado_texto"] = "Offline (Tempo Correndo)"
                            
                    else:
                        dados["time_left"] = "-"
                        if is_online:
                            dados["estado_texto"] = "Conectado S/ Autorizacao"
                        else:
                            dados["estado_texto"] = "Offline"
                
                conexao.disconnect()

                # Desconecta os usuários que esgotaram o tempo
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

# === ROTAS DO MENU E SISTEMA GERAL ===
@app.route('/admin/login', methods=['POST'])
def admin_login():
    senha = request.json.get('senha')
    if senha == WEB_ADMIN_PASS:
        return jsonify({"sucesso": True})
    return jsonify({"sucesso": False, "mensagem": "Senha incorreta."})

@app.route('/admin/config_sys', methods=['GET', 'POST'])
def sys_config():
    if request.method == 'GET':
        return jsonify(config_sistema)
    
    dados = request.json
    if dados.get('senha') != WEB_ADMIN_PASS:
        return jsonify({"sucesso": False, "mensagem": "Acesso negado."})
    
    config_sistema["gerenciar_tempo_script"] = dados.get('gerenciar_tempo_script', True)
    return jsonify({"sucesso": True, "mensagem": "Configuração salva!"})

@app.route('/admin/get_profiles', methods=['POST'])
def get_profiles():
    if request.json.get('senha') != WEB_ADMIN_PASS:
        return jsonify({"sucesso": False, "mensagem": "Acesso negado."})
    
    try:
        conexao = routeros_api.RouterOsApiPool(MK_IP, username=MK_USER, password=MK_PASS, plaintext_login=True)
        api = conexao.get_api()
        profiles = api.get_resource('/ip/hotspot/user/profile').get()
        conexao.disconnect()
        return jsonify({"sucesso": True, "profiles": profiles})
    except Exception as e:
        return jsonify({"sucesso": False, "mensagem": str(e)})

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
            rate_limit = dados.get('rate_limit')
            
            recurso_profile = api.get_resource('/ip/hotspot/user/profile')
            existente = recurso_profile.get(name=nome_perfil)
            
            parametros = {'session-timeout': session_time, 'rate-limit': rate_limit, 'shared-users': '1'}
            parametros = {k: v for k, v in parametros.items() if v != ""}
            
            if existente:
                recurso_profile.set(id=existente[0]['id'], **parametros)
                msg = f"Perfil '{nome_perfil}' atualizado com sucesso!"
            else:
                recurso_profile.add(name=nome_perfil, **parametros)
                msg = f"Perfil '{nome_perfil}' criado com sucesso!"
                
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
        
        #toast { visibility: hidden; min-width: 250px; background-color: #333; color: #fff; text-align: center; border-radius: 5px; padding: 16px; position: fixed; z-index: 9999; right: 20px; top: 20px; box-shadow: 0 4px 8px rgba(0,0,0,0.2); transition: 0.3s; }
        #toast.show { visibility: visible; }
        #toast.success { background-color: #10b981; }
        #toast.error { background-color: #ef4444; }

        .menu-btn { position: absolute; top: 20px; left: 20px; background: #2563eb; color: white; border: none; padding: 10px 18px; border-radius: 8px; cursor: pointer; font-weight: bold; transition: 0.2s; display: flex; align-items: center; gap: 8px; }
        .menu-btn:hover { background: #1d4ed8; box-shadow: 0 4px 10px rgba(37, 99, 235, 0.3); }
        
        .modal-overlay { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(15, 23, 42, 0.6); z-index: 50; backdrop-filter: blur(4px); }
        .modal { position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%) scale(0.95); background: white; padding: 25px; border-radius: 12px; width: 90%; z-index: 51; box-shadow: 0 20px 40px rgba(0,0,0,0.2); display: none; opacity: 0; transition: all 0.3s ease; }
        .modal.active { display: block; opacity: 1; transform: translate(-50%, -50%) scale(1); }
        .modal-overlay.active { display: block; }
        
        #modalLogin { max-width: 350px; text-align: center; }
        #modalConfig { max-width: 800px; max-height: 90vh; overflow-y: auto; }

        .modal h3 { margin-top: 0; color: #1e293b; border-bottom: 2px solid #f1f5f9; padding-bottom: 12px; font-size: 20px; }
        .btn-fechar { position: absolute; top: 15px; right: 20px; background: #f1f5f9; border: none; font-size: 18px; cursor: pointer; color: #64748b; border-radius: 50%; width: 30px; height: 30px; display: flex; align-items: center; justify-content: center; transition: 0.2s; }
        .btn-fechar:hover { background: #e2e8f0; color: #ef4444; }

        .form-group { margin-bottom: 15px; text-align: left; }
        .form-group label { display: block; font-size: 13px; color: #475569; margin-bottom: 6px; font-weight: 600; }
        .form-group input { width: 100%; padding: 10px 12px; border: 1px solid #cbd5e1; border-radius: 8px; font-size: 14px; outline: none; transition: 0.2s; }
        .form-group input:focus { border-color: #2563eb; box-shadow: 0 0 0 3px rgba(37,99,235,0.1); }

        .sys-config-box { background: #f8fafc; padding: 15px; border-radius: 8px; margin-bottom: 25px; border: 1px solid #e2e8f0; }
        .sys-config-box label { display: flex; align-items: center; gap: 10px; cursor: pointer; font-size: 14px; color: #334155; font-weight: bold;}

        .rb-table { width: 100%; border-collapse: collapse; margin-top: 10px; margin-bottom: 25px; font-size: 13px; }
        .rb-table th { background: #f8fafc; color: #475569; padding: 12px; border-bottom: 2px solid #e2e8f0; text-align: left; }
        .rb-table td { padding: 12px; border-bottom: 1px solid #f1f5f9; color: #334155; }
        .rb-table tr:hover { background-color: #f8fafc; }
        .btn-edit { background: #f59e0b; color: white; border: none; padding: 6px 12px; border-radius: 5px; cursor: pointer; font-size: 12px; font-weight: bold; }
        .btn-edit:hover { background: #d97706; }

        .btn { padding: 10px 15px; border: none; border-radius: 8px; cursor: pointer; font-weight: bold; font-size: 13px; transition: 0.2s; color: white; }
        .btn-green { background-color: #10b981; } .btn-green:hover { background-color: #059669; }
        .btn-blue { background-color: #3b82f6; } .btn-blue:hover { background-color: #2563eb; }
        .btn-red { background-color: #ef4444; } .btn-red:hover { background-color: #dc2626; }
        .acoes { display: flex; flex-wrap: wrap; justify-content: center; gap: 5px; }

        .tabs { display: flex; justify-content: center; gap: 10px; margin-bottom: 20px; margin-top: 20px; }
        .tab-btn { padding: 10px 20px; border: none; border-radius: 8px; cursor: pointer; font-weight: bold; background-color: #e2e8f0; color: #475569; transition: 0.3s; }
        .tab-btn.active { background-color: #2563eb; color: #fff; }

        .status-badge { padding: 4px 8px; border-radius: 12px; font-size: 12px; font-weight: bold; color: white; display: inline-block; }
        .badge-pendente { background-color: #f59e0b; }
        .badge-aprovado { background-color: #10b981; }
        .badge-recusado, .badge-desconectado { background-color: #ef4444; }

        #tabela-solicitacoes { width: 100%; border-collapse: collapse; margin-top: 15px; font-size: 14px; }
        #tabela-solicitacoes th, #tabela-solicitacoes td { padding: 15px 10px; border-bottom: 1px solid #eee; text-align: center; vertical-align: middle; }
        #tabela-solicitacoes th { background-color: #f8fafc; color: #64748b; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
        
        .live-indicator { display: inline-block; width: 8px; height: 8px; background-color: #10b981; border-radius: 50%; margin-right: 5px; animation: blink 1.5s infinite; }
        @keyframes blink { 0% {opacity: 1;} 50% {opacity: 0.4;} 100% {opacity: 1;} }
        
        .conexao-info { font-size: 13px; margin-top: 5px; font-weight: 600; padding: 4px; border-radius: 5px;}
        .estado-verde { color: #10b981; background-color: #ecfdf5;}
        .estado-amarelo { color: #d97706; background-color: #fffbeb;}
        .estado-cinza { color: #64748b; background-color: #f1f5f9;}
        .estado-vermelho { color: #ef4444; background-color: #fef2f2;}
        .tempo { color: #64748b; font-size: 11px; font-weight: normal; display: block; margin-top: 3px;}
        
        .grid-forms { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }
        @media(max-width: 600px) { .grid-forms { grid-template-columns: 1fr; } }
    </style>
</head>
<body>
    <div id="toast">Notificação</div>

    <div class="modal-overlay" id="modalOverlay" onclick="fecharModais()"></div>

    <div class="modal" id="modalLogin">
        <button class="btn-fechar" onclick="fecharModais()">&times;</button>
        <h3 style="border:none; margin-bottom:5px;">🔒 Acesso Restrito</h3>
        <p style="font-size: 13px; color:#64748b; margin-bottom: 20px;">Insira a senha para gerenciar o MikroTik.</p>
        <div class="form-group">
            <input type="password" id="inputSenha" placeholder="Digite sua senha" onkeypress="if(event.key === 'Enter') fazerLogin()">
        </div>
        <button class="btn btn-blue" style="width: 100%;" onclick="fazerLogin()">Entrar</button>
    </div>

    <div class="modal" id="modalConfig">
        <button class="btn-fechar" onclick="fecharModais()">&times;</button>
        <h3>⚙️ Gerenciador de Sistema e RB</h3>
        
        <div class="sys-config-box">
            <h4 style="margin: 0 0 10px 0; color: #1e293b; font-size: 15px;">Gestão Automática</h4>
            <label>
                <input type="checkbox" id="chkGerenciarTempo" onchange="salvarConfigSys()" style="width: 18px; height: 18px;">
                Deixar o Script Python derrubar quem esgotar o tempo (Desmarque se a RB já faz isso nativamente pelo Profile)
            </label>
        </div>

        <h4 style="margin: 0 0 10px 0; color: #334155;">Perfis Existentes na RouterBoard</h4>
        <div style="max-height: 200px; overflow-y: auto; border: 1px solid #e2e8f0; border-radius: 8px; margin-bottom: 20px;">
            <table class="rb-table">
                <thead>
                    <tr>
                        <th>Nome</th>
                        <th>Tempo Limite</th>
                        <th>Banda (Queue)</th>
                        <th>Ação</th>
                    </tr>
                </thead>
                <tbody id="listaPerfisCorpo">
                    <tr><td colspan="4" style="text-align:center;">Carregando perfis...</td></tr>
                </tbody>
            </table>
        </div>

        <h4 style="margin: 0 0 10px 0; color: #334155;" id="formTitle">Criar / Modificar Perfil</h4>
        <div class="grid-forms">
            <div class="form-group">
                <label>Nome do Perfil</label>
                <input type="text" id="mk_nome_perfil" placeholder="Ex: Visitante-1h">
            </div>
            <div class="form-group">
                <label>Tempo de Sessão</label>
                <input type="text" id="mk_session_time" placeholder="Ex: 01:00:00 (vazio = ilimitado)">
            </div>
        </div>
        <div class="form-group">
            <label>Limite de Banda (Rx/Tx)</label>
            <input type="text" id="mk_rate_limit" placeholder="Ex: 10M/10M (vazio = ilimitado)">
        </div>
        
        <div style="display: flex; gap: 10px;">
            <button class="btn btn-blue" style="flex: 1;" onclick="salvarPerfilRB()">💾 Salvar Perfil na RB</button>
            <button class="btn" style="background:#e2e8f0; color:#475569;" onclick="limparFormulario()">Limpar</button>
        </div>
    </div>

    <div class="container">
        <button class="menu-btn" onclick="abrirMenuLogin()">
            <svg width="16" height="16" fill="currentColor" viewBox="0 0 16 16"><path fill-rule="evenodd" d="M2.5 12a.5.5 0 0 1 .5-.5h10a.5.5 0 0 1 0 1H3a.5.5 0 0 1-.5-.5zm0-4a.5.5 0 0 1 .5-.5h10a.5.5 0 0 1 0 1H3a.5.5 0 0 1-.5-.5zm0-4a.5.5 0 0 1 .5-.5h10a.5.5 0 0 1 0 1H3a.5.5 0 0 1-.5-.5z"/></svg>
            Menu RB
        </button>
        
        <h2>Painel de Gerência Hotspot</h2>
        <div style="text-align: center; margin-bottom: 20px; color: #64748b; font-size: 13px;">
            <span class="live-indicator"></span> Monitoramento Inteligente: Analisando conexões...
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
        let authPass = '';

        function showToast(msg, tipo) {
            const toast = document.getElementById("toast");
            toast.className = "show " + tipo;
            toast.innerText = msg;
            setTimeout(() => { toast.className = toast.className.replace("show", ""); }, 3000);
        }

        /* --- LOGICA MODAL E LOGIN --- */
        function fecharModais() {
            document.getElementById('modalOverlay').classList.remove('active');
            document.getElementById('modalLogin').classList.remove('active');
            document.getElementById('modalConfig').classList.remove('active');
        }

        function abrirMenuLogin() {
            if(authPass) {
                abrirPainelRB();
            } else {
                document.getElementById('inputSenha').value = '';
                document.getElementById('modalOverlay').classList.add('active');
                document.getElementById('modalLogin').classList.add('active');
                setTimeout(() => document.getElementById('inputSenha').focus(), 100);
            }
        }

        function fazerLogin() {
            const senha = document.getElementById('inputSenha').value;
            if(!senha) return showToast("Digite a senha!", "error");

            fetch('/admin/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ senha: senha })
            })
            .then(res => res.json())
            .then(data => {
                if(data.sucesso) {
                    authPass = senha;
                    showToast("Acesso Liberado!", "success");
                    document.getElementById('modalLogin').classList.remove('active');
                    abrirPainelRB();
                } else {
                    showToast(data.mensagem, "error");
                }
            }).catch(() => showToast("Erro de rede.", "error"));
        }

        /* --- GESTAO DO SISTEMA E RB --- */
        function abrirPainelRB() {
            document.getElementById('modalOverlay').classList.add('active');
            document.getElementById('modalConfig').classList.add('active');
            carregarConfigSys();
            carregarPerfisRB();
        }

        function carregarConfigSys() {
            fetch('/admin/config_sys')
            .then(res => res.json())
            .then(data => {
                document.getElementById('chkGerenciarTempo').checked = data.gerenciar_tempo_script;
            });
        }

        function salvarConfigSys() {
            const isChecked = document.getElementById('chkGerenciarTempo').checked;
            fetch('/admin/config_sys', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ senha: authPass, gerenciar_tempo_script: isChecked })
            })
            .then(res => res.json())
            .then(data => {
                if(data.sucesso) {
                    showToast("Lógica do sistema atualizada!", "success");
                } else {
                    showToast(data.mensagem, "error");
                    document.getElementById('chkGerenciarTempo').checked = !isChecked; // desfaz se der erro
                }
            });
        }

        function carregarPerfisRB() {
            const tbody = document.getElementById('listaPerfisCorpo');
            tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;">Buscando dados na RB...</td></tr>';
            
            fetch('/admin/get_profiles', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ senha: authPass })
            })
            .then(res => res.json())
            .then(data => {
                if(!data.sucesso) {
                    tbody.innerHTML = `<tr><td colspan="4" style="text-align:center;color:red;">${data.mensagem}</td></tr>`;
                    return;
                }
                
                tbody.innerHTML = '';
                if(data.profiles.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;">Nenhum perfil encontrado.</td></tr>';
                } else {
                    data.profiles.forEach(prof => {
                        const nome = prof.name || '-';
                        const tempo = prof['session-timeout'] || 'Ilimitado';
                        const banda = prof['rate-limit'] || 'Ilimitado';
                        
                        let tr = document.createElement('tr');
                        tr.innerHTML = `
                            <td><strong>${nome}</strong></td>
                            <td>${tempo}</td>
                            <td>${banda}</td>
                            <td><button class="btn-edit" onclick="preencherFormEdit('${nome}', '${tempo}', '${banda}')">✏️ Editar</button></td>
                        `;
                        tbody.appendChild(tr);
                    });
                }
            });
        }

        function preencherFormEdit(nome, tempo, banda) {
            document.getElementById('formTitle').innerText = "Modificar Perfil Existente";
            document.getElementById('mk_nome_perfil').value = nome;
            document.getElementById('mk_session_time').value = tempo === 'Ilimitado' ? '' : tempo;
            document.getElementById('mk_rate_limit').value = banda === 'Ilimitado' ? '' : banda;
            document.getElementById('mk_session_time').focus();
        }

        function limparFormulario() {
            document.getElementById('formTitle').innerText = "Criar Novo Perfil";
            document.getElementById('mk_nome_perfil').value = '';
            document.getElementById('mk_session_time').value = '';
            document.getElementById('mk_rate_limit').value = '';
        }

        function salvarPerfilRB() {
            const btn = event.target;
            const originalText = btn.innerHTML;
            btn.innerHTML = "⏳ Salvando...";
            
            const dados = {
                senha: authPass,
                acao: 'criar_perfil',
                nome_perfil: document.getElementById('mk_nome_perfil').value,
                session_time: document.getElementById('mk_session_time').value,
                rate_limit: document.getElementById('mk_rate_limit').value
            };

            if(!dados.nome_perfil) {
                btn.innerHTML = originalText;
                return showToast("O Nome do perfil é obrigatório!", "error");
            }

            fetch('/admin/config_mk', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(dados)
            })
            .then(res => res.json())
            .then(data => {
                btn.innerHTML = originalText;
                if(data.sucesso) {
                    showToast(data.mensagem, "success");
                    limparFormulario();
                    carregarPerfisRB(); 
                } else {
                    showToast(data.mensagem, "error");
                    if(data.mensagem.includes("Senha")) { authPass = ''; fecharModais(); }
                }
            }).catch(e => {
                btn.innerHTML = originalText;
                showToast("Erro de conexão.", "error");
            });
        }

        /* --- VISUALIZACAO GERAL DE CONEXOES --- */
        function mudarAba(aba) {
            filtroAtual = aba;
            document.getElementById('btn-online').classList.remove('active');
            document.getElementById('btn-offline').classList.remove('active');
            document.getElementById('btn-' + aba).classList.add('active');
            carregarDadosVis();
        }

        function fazerAcao(acao, mac) {
            event.target.innerText = "⏳...";
            event.target.style.opacity = "0.5";

            fetch('/admin/acao', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ acao: acao, mac: mac })
            })
            .then(res => res.json())
            .then(data => {
                if(data.sucesso) {
                    showToast("Ação confirmada!", "success");
                    carregarDadosVis();
                } else {
                    showToast("Erro: " + data.mensagem, "error");
                    carregarDadosVis();
                }
            });
        }

        function carregarDadosVis() {
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

        setInterval(carregarDadosVis, 3000);
        window.onload = carregarDadosVis;
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
