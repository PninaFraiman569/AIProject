FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE_PATH=/app/data/lab_data.db

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN useradd -m appuser && mkdir -p /app/data && chown -R appuser /app
USER appuser

# מסד הנתונים נשמר כאן - לחבר דיסק קבוע (volume) כדי שלא יימחק בהפעלה מחדש
VOLUME /app/data
EXPOSE 8501
HEALTHCHECK CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://localhost:{os.environ.get(\"PORT\",\"8501\")}/_stcore/health')" || exit 1

# PORT מוגדר אוטומטית ע"י חלק מספקי הענן (Render, Railway, Cloud Run)
CMD ["sh", "-c", "streamlit run app.py --server.port=${PORT:-8501} --server.address=0.0.0.0"]
