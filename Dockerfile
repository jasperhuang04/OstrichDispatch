FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock && useradd --uid 10001 --create-home parcel
COPY --chown=parcel:parcel . .
RUN mkdir -p /app/data /app/staticfiles && chown -R parcel:parcel /app/data /app/staticfiles
USER parcel
EXPOSE 8000
CMD ["gunicorn", "parcelnest.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "2", "--timeout", "90"]
