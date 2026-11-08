FROM python:3.12-slim
WORKDIR /app
COPY services/question-service/main.py .
COPY flags.json .
ENV PORT=8001
EXPOSE 8001
CMD ["python3", "main.py"]
