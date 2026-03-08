from minio import Minio
# from minio.error import S3Error

# MinIO connection details
MINIO_URL = "localhost:9000"            # or the container name if running in Docker network: "minio:9000"
ACCESS_KEY = "laiadmin"
SECRET_KEY = "superStrongPassword123!"
USE_SSL = False                         # Set to True if you're using HTTPS

def check_minio_connection():
    try:
        client = Minio(
            MINIO_URL,
            access_key=ACCESS_KEY,
            secret_key=SECRET_KEY,
            secure=USE_SSL
        )

        # Just trying to list buckets is enough to verify connection & authentication
        client.list_buckets()

        print("Connection successful")
    except Exception as e:
        print("Connection failed:", str(e))


if __name__ == "__main__":
    check_minio_connection()
