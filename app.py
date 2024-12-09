from flask import Flask, request, jsonify, session
from flask_cors import CORS
import mysql.connector
import datetime
import jwt
import logging
from sklearn.preprocessing import LabelEncoder
import joblib
import random

app = Flask(__name__)
CORS(app, supports_credentials=True)

app.config['SECRET_KEY'] = 'sistemrekomendasikegiatan-api#2024'

# Database configuration
db_config = {
    "host": "localhost",
    "user": "root",
    "password": "",
    "database": "sistemrekomendasikegiatan"
}

# Global MySQL connection
connection = None

# Logger setup
logging.basicConfig(level=logging.DEBUG)


def get_db_connection():
    global connection
    # Check if the connection is still open
    if connection is None or not connection.is_connected():
        try:
            connection = mysql.connector.connect(**db_config)
            logging.debug("MySQL connection established.")
        except mysql.connector.Error as err:
            logging.error(f"MySQL connection error: {err}")
            raise Exception("Database connection error.")
    return connection


def load_model():
    try:
        return joblib.load('tuned_svd_model.joblib')
    except Exception as e:
        logging.error(f"Model loading error: {e}")
        raise


@app.route('/')
def main():
    return jsonify({"message": "Hello from flask"})


@app.route('/activities')
def activities():
    try:
        query = "SELECT DISTINCT nama_kegiatan, kategori FROM dataset_kegiatanmahasiswa LIMIT 6"
        cursor = get_db_connection().cursor(dictionary=True)
        cursor.execute(query)
        activities = cursor.fetchall()

        return jsonify({"status": True, "message": "Data Kegiatan", "data": activities}), 200
    except Exception as err:
        logging.error(f"Error fetching activities: {err}")
        return jsonify({"status": False, "error": str(err)}), 500
    finally:
        if connection:
            connection.close()


@app.route('/login', methods=['POST'])
def login():
    try:
        data = request.json
        npm_mahasiswa = data.get('npm_mahasiswa')
        password = data.get('password')

        if not npm_mahasiswa or not password:
            return jsonify({"status": "error", "message": "npm_mahasiswa and password are required"}), 400

        # Cari mahasiswa di database
        query = "SELECT * FROM dataset_mahasiswa WHERE npm_mahasiswa = %s"
        cursor = get_db_connection().cursor(dictionary=True)
        cursor.execute(query, (npm_mahasiswa,))
        mhs = cursor.fetchone()

        if not mhs:
            return jsonify({"status": "error", "message": "mhs not found"}), 404

        # Validasi password (misalnya menggunakan bcrypt untuk perbandingan)
        if password != mhs['npm_mahasiswa']:  # Periksa apakah password sesuai dengan npm_mahasiswa
            return jsonify({"status": "error", "message": "Invalid credentials"}), 401

        # Membuat JWT Token
        payload = {
            'npm_mahasiswa': mhs['npm_mahasiswa'],
            'nama_mahasiswa': mhs['nama_mahasiswa'],
            'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=1)  # Token akan kedaluwarsa dalam 1 jam
        }
        token = jwt.encode(payload, app.config['SECRET_KEY'], algorithm='HS256')

        # Simpan data pengguna di sesi (opsional, jika Anda juga ingin menggunakan session)
        session['npm_mahasiswa'] = mhs['npm_mahasiswa']
        session['nama_mahasiswa'] = mhs['nama_mahasiswa']

        # Kirim token ke client
        return jsonify({
            "status": "success",
            "message": "Login successful",
            "npm_mahasiswa": mhs['npm_mahasiswa'],
            "nama_mahasiswa": mhs['nama_mahasiswa'],
            "token": token  # Kirim token ke frontend
        })

    except Exception as e:
        logging.error(f"Login error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({"status": "success", "message": "Logout successful"})


@app.route('/recommendations', methods=['GET'])
def recommendations():
    try:
        # Ambil token dari header Authorization
        token = request.headers.get('Authorization')
        if not token:
            return jsonify({"error": "Token is required"}), 400

        if token.startswith('Bearer '):
            token = token[7:]

        # Decode token untuk mendapatkan npm_mahasiswa
        try:
            decoded_token = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
            npm_mahasiswa = decoded_token['npm_mahasiswa']
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token has expired"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "Invalid token"}), 401

        # Pastikan model telah dimuat
        model = load_model()
        if model is None:
            return jsonify({"error": "Model not loaded"}), 500

        le = LabelEncoder()

        # Hubungkan ke database
        cursor = get_db_connection().cursor(dictionary=True)

        # Ambil semua kategori dari dataset_kegiatanmahasiswa
        cursor.execute("SELECT DISTINCT kategori FROM dataset_kegiatanmahasiswa")
        all_activities = cursor.fetchall()

        if not all_activities:
            return jsonify({"error": "No activities found"}), 404

        # Ambil kegiatan yang sudah diikuti mahasiswa berdasarkan npm_mahasiswa
        query_student_activities = """
        SELECT kategori_matakuliah
        FROM dataset_krs
        WHERE npm_mahasiswa = %s
        """
        cursor.execute(query_student_activities, (npm_mahasiswa,))
        taken_activities = {row['kategori_matakuliah'] for row in cursor.fetchall()}

        # Fit LabelEncoder dengan semua kategori dari dataset_kegiatanmahasiswa
        all_kategoris = [activity['kategori'] for activity in all_activities]
        le.fit(all_kategoris)

        # Persiapkan prediksi untuk kegiatan yang belum diikuti
        predictions = []
        for activity in all_activities:
            kategori = activity['kategori']
            if kategori not in taken_activities:
                # Encode kategori
                kategori_encoded = le.transform([kategori])[0]
                # Lakukan prediksi menggunakan model
                prediction = model.predict(npm_mahasiswa, kategori_encoded)
                predictions.append({
                    "kategori": kategori,
                    "predicted_rating": prediction.est
                })

        # Acak urutan prediksi
        random.shuffle(predictions)

        # Ambil 5 rekomendasi acak
        top_recommendations = predictions[:6]

        # Cari kegiatan relevan berdasarkan kategori rekomendasi
        relevant_activities = {}
        for recommendation in top_recommendations:
            kategori = recommendation['kategori']
            cursor.execute("SELECT nama_kegiatan FROM dataset_kegiatanmahasiswa WHERE kategori = %s LIMIT 6", (kategori,))
            activities_for_category = cursor.fetchall()
            relevant_activities[kategori] = [activity['nama_kegiatan'] for activity in activities_for_category]

        # Kirimkan rekomendasi bersama kegiatan relevan
        return jsonify({
            "npm_mahasiswa": npm_mahasiswa,
            "recommendations": [
                {
                    "kategori": recommendation['kategori'],
                    "predicted_rating": recommendation['predicted_rating'],
                    "relevant_activities": relevant_activities.get(recommendation['kategori'], [])
                }
                for recommendation in top_recommendations
            ]
        }), 200

    except Exception as e:
        logging.error(f"Recommendation error: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        if connection:
            connection.close()

@app.route('/get_student_data')
def get_student_data():
    try:
        token = request.headers.get('Authorization')
        
        if not token: 
            return jsonify({"status" : "false", "message" : "Please provide a token"}), 400
        
        if token.startswith('Bearer '):
            token = token[7:]  # Remove the 'Bearer ' prefix
            
        try:
            decoded_token = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
            npm_mahasiswa = decoded_token['npm_mahasiswa']
        except jwt.ExpiredSignatureError:
            return jsonify({"status": "error", "message": "Token has expired"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"status": "error", "message": "Invalid token"}), 401
        
        cursor = connection.cursor(dictionary=True)
          
        query = """
        SELECT nama_mahasiswa, prodi_mahasiswa
        FROM dataset_mahasiswa WHERE npm_mahasiswa = %s
        """
        cursor.execute(query , (npm_mahasiswa,))
        student = cursor.fetchone()

        if student:
            return jsonify({"success": True, "data": student})
        else:
            return jsonify({"success": False, "error": "Student not found."}), 404
    except Exception as e:
        return jsonify({"success": False}), 500
    finally:
        if connection is None:
            connection.close()




@app.route('/get_detail_student_data')
def get_student_detail_data():
    try:
        # Ambil token dari header
        token = request.headers.get('Authorization')
        if not token:
            return jsonify({"status": False, "message": "Please enter token"}), 400

        if token.startswith('Bearer '):
            token = token[7:]
            
        try:
            # Decode token
            decoded_token = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
            npm_mahasiswa = decoded_token['npm_mahasiswa']
        except jwt.ExpiredSignatureError:
            return jsonify({"status": "error", "message": "Token has expired"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"status": "error", "message": "Invalid token"}), 401
        
        cursor = connection.cursor(dictionary=True)
        
        # Query untuk mendapatkan data mahasiswa dan 3 mata kuliah pertama
        query_mahasiswa = """
        SELECT nama_mahasiswa, npm_mahasiswa, prodi_mahasiswa, angkatan_mahasiswa, status_mahasiswa
        FROM dataset_mahasiswa 
        WHERE npm_mahasiswa = %s
        """
        
        cursor.execute(query_mahasiswa, (npm_mahasiswa,))
        student = cursor.fetchone()  # Ambil hanya satu mahasiswa
        
        if not student:
            return jsonify({"success": False, "error": "Student not found."}), 404
        
        # Query untuk mengambil 3 mata kuliah mahasiswa
        query_mata_kuliah = """
        SELECT nama_matkul, kode_matkul, kategori_matakuliah, sks_matakuliah, kode_nilai , total_pertemuan , total_hadir , total_tidak_hadir
        FROM dataset_krs 
        WHERE npm_mahasiswa = %s
        LIMIT 3  -- Ambil 3 mata kuliah saja
        """
        
        cursor.execute(query_mata_kuliah, (npm_mahasiswa,))
        mata_kuliah = cursor.fetchall()  # Ambil 3 mata kuliah
        
        if mata_kuliah:
            return jsonify({
                "success": True,
                "data": {
                    "student": student,
                    "mata_kuliah": mata_kuliah
                }
            }), 200
        else:
            return jsonify({"success": False, "error": "No courses found for student."}), 404

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if connection is None:
            connection.close()


if __name__ == '__main__':
    app.run(debug=True)
