# Base image chinh thuc cua Apify cho Python actor CO SAN Playwright + trinh duyet.
# Khac voi actor Agoda (dung apify/actor-python thuan, khong browser) vi Booking.com
# can 1 buoc "bootstrap cookie" bang headless Chromium de vuot AWS WAF JS-challenge.
FROM apify/actor-python-playwright:3.13

COPY requirements.txt ./

RUN echo "Python version:" \
 && python --version \
 && echo "Installing dependencies:" \
 && pip install --no-cache-dir -r requirements.txt \
 && echo "All installed packages:" \
 && pip freeze

COPY . ./

CMD ["python", "-m", "src"]
