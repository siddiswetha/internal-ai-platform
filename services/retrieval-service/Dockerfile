FROM python:3.12-slim
WORKDIR /app
COPY services/retrieval-service/main.py .
COPY docs ./docs
ENV PORT=8002
EXPOSE 8002
CMD ["python3", "main.py"]
