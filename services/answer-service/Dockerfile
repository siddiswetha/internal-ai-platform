FROM python:3.12-slim
WORKDIR /app
COPY services/answer-service/main.py .
ENV PORT=8003
EXPOSE 8003
CMD ["python3", "main.py"]
