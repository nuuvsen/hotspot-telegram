# Usa uma imagem oficial do Python, leve e compatível com a TV Box (ARM)
FROM python:3.9-slim

# Define a pasta de trabalho dentro do contêiner
WORKDIR /app

# Copia o arquivo de dependências e instala
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o restante dos arquivos do projeto
COPY . .

# Expõe a porta 5000
EXPOSE 5000

# Comando para rodar o bot
CMD ["python", "portaria_bot.py"]