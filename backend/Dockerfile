FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# включить перед первым запуском чтобы бд наполнилась данными
# RUN python load_data.py

# пока fastapi в теории можно перейти на что угодно
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--log-level", "debug"]
