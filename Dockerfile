# Use slim Python base image
FROM python:3.12-slim

RUN mkdir /app
WORKDIR /app
COPY requirements.txt /app/

# Set environment variables for non-interactive installs
ENV DEBIAN_FRONTEND=noninteractive \
    ACCEPT_EULA=Y \
    TZ=Etc/UTC

# Install dependencies for ODBC and Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    apt-transport-https \
    ca-certificates \
    unixodbc \
    unixodbc-dev \
    build-essential \
    lsb-release \
    wget \
    git \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

# Add Microsoft ODBC repo and key
RUN curl https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor > /usr/share/keyrings/microsoft.gpg && \
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" > /etc/apt/sources.list.d/mssql-release.list

# Update package lists and install ODBC driver
RUN apt-get update && apt-get install -y msodbcsql17 \
    && rm -rf /var/lib/apt/lists/*



RUN pip install --upgrade pip
RUN pip install --upgrade setuptools
RUN pip install -r /app/requirements.txt
RUN apt install curl ca-certificates
RUN install -d /usr/share/postgresql-common/pgdg
RUN curl -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc --fail https://www.postgresql.org/media/keys/ACCC4CF8.asc
RUN sh -c 'echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" > /etc/apt/sources.list.d/pgdg.list'
RUN apt update
RUN apt -y install postgresql-client-16