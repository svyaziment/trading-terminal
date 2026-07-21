import os
import socket
import sys


def main() -> None:
    host = os.getenv("POSTGRES_HOST", "").strip()
    port_raw = os.getenv("POSTGRES_PORT", "5432").strip() or "5432"

    try:
        port = int(port_raw)
    except ValueError:
        port = 5432

    user = os.getenv("POSTGRES_USER", "").strip()
    database = os.getenv("POSTGRES_DB", "").strip()
    password_set = bool(os.getenv("PSTGRS_PWD", "").strip())

    dns_ok = "false"
    tcp_ok = "false"
    error_message = ""

    if not host:
        error_message = "POSTGRES_HOST is empty"
    else:
        try:
            socket.getaddrinfo(host, port)
            dns_ok = "true"
        except Exception as exc:
            error_message = str(exc).replace("\n", " ")

        if dns_ok == "true":
            try:
                sock = socket.create_connection((host, port), timeout=5)
                sock.close()
                tcp_ok = "true"
            except Exception as exc:
                if not error_message:
                    error_message = str(exc).replace("\n", " ")

    print("ENV_POSTGRES_HOST=" + host)
    print("ENV_POSTGRES_PORT=" + str(port))
    print("ENV_POSTGRES_USER=" + user)
    print("ENV_POSTGRES_DB=" + database)
    print("PSTGRS_PWD_SET=" + ("true" if password_set else "false"))
    print("DNS_OK=" + dns_ok)
    print("TCP_OK=" + tcp_ok)
    print("ERROR_MESSAGE=" + error_message)

    sys.exit(0)


if __name__ == "__main__":
    main()
