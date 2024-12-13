from flask import Flask, request, jsonify, session
from flask_cors import CORS
import mysql.connector
from mysql.connector import Error
import datetime
import jwt
import time
import logging
from sklearn.preprocessing import LabelEncoder
import joblib
import random

app = Flask(__name__)
CORS(app)

app.config['SECRET_KEY'] = 'sistemrekomendasikegiatan-api#2024'

# Database configuration
db_config = {
    "host": "localhost",
    "user": "root",
    "password": "",
    "database": "sistemrekomendasikegiatan"
}



def get_db_connection():
    return mysql.connector.connect(**db_config)

def execute_query_with_retry(query, params=None, max_retries=3, delay=1):
    for attempt in range(max_retries):
        try:
            connection = get_db_connection()
            cursor = connection.cursor(dictionary=True)
            cursor.execute(query, params)
            result = cursor.fetchall()
            connection.commit()
            return result
        except Error as e:
            logging.error(f"Database error on attempt {attempt + 1}: {e}")
            if attempt == max_retries - 1:
                raise
            time.sleep(delay)
        finally:
            if 'connection' in locals() and connection.is_connected():
                cursor.close()
                connection.close()

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
        token = request.headers.get('Authorization')
        if not token:
            return jsonify({"message": "Please provide token"})
        if token.startswith('Bearer '):
            token = token[7:]
        try:
            decoded_token = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
            npm_mahasiswa = decoded_token['npm_mahasiswa']
            nama_mahasiswa = decoded_token['nama_mahasiswa']
        except jwt.ExpiredSignatureError:
            return jsonify({"status": "error", "message": "Token has expired"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"status": "error", "message": "Invalid token"}), 401
        
        query = "SELECT DISTINCT nama_kegiatan, kategori FROM dataset_kegiatanmahasiswa WHERE npm_mahasiswa = %s LIMIT 6"
        max_retries = 3
        retry_delay = 1

        for attempt in range(max_retries):
            try:
                connection = get_db_connection()
                cursor = connection.cursor(dictionary=True)
                cursor.execute(query, (npm_mahasiswa,))
                activities = cursor.fetchall()
            
                return jsonify({"status": True, "message": "Data Kegiatan", "data": activities}), 200

            except mysql.connector.Error as e:
                logging.error(f"Database error on attempt {attempt + 1}: {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                else:
                    return jsonify({"status": False, "error": "Database error after max retries"}), 500

            except Exception as err:
                logging.error(f"Unexpected error: {err}")
                return jsonify({"status": False, "error": "Unexpected error occurred"}), 500

            finally:
                if 'connection' in locals() and connection.is_connected():
                    cursor.close()
                    connection.close()
    except Exception as e:
    # This line should never be reached, but it's here for completeness
        return jsonify({"status": False, "error": "Unknown error occurred"}), 500


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
        query_kegiatan = "SELECT DISTINCT kategori FROM dataset_kegiatanmahasiswa"

        # Ambil semua kategori dari dataset_kegiatanmahasiswa
        all_activities = execute_query_with_retry(query_kegiatan)
        

        if not all_activities:
            return jsonify({"error": "No activities found"}), 404

        # Ambil kegiatan yang sudah diikuti mahasiswa berdasarkan npm_mahasiswa
        query_student_activities = """
        SELECT kategori_matakuliah
        FROM dataset_krs
        WHERE npm_mahasiswa = %s
        """
        taken_activities = execute_query_with_retry(query_student_activities , (npm_mahasiswa,))
        taken_activities = {row['kategori_matakuliah'] for row in taken_activities}

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
            activities = execute_query_with_retry(
                "SELECT nama_kegiatan FROM dataset_kegiatanmahasiswa WHERE kategori = %s LIMIT 6",
                (kategori,)
            )
            relevant_activities[kategori] = [activity['nama_kegiatan'] for activity in activities]

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

    except Error as e:
        logging.error(f"Database error: {e}")
        return jsonify({"status": False, "error": "Database error"}), 500
    except Exception as err:
        logging.error(f"Unexpected error: {err}")
        return jsonify({"status": False, "error": "Unexpected error occurred"}), 500
            

@app.route('/get_student_data')
def get_student_data():
    global connection  # Tambahkan ini agar connection bisa diakses dalam fungsi
    
    try:
        token = request.headers.get('Authorization')
        
        if not token: 
            return jsonify({"status" : "false", "message" : "Please provide a token"}), 400
        
        if token.startswith('Bearer '):
            token = token[7:]  # Remove the 'Bearer ' prefix
            
        try:
            decoded_token = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
            npm_mahasiswa = decoded_token['npm_mahasiswa']
            nama_mahasiswa = decoded_token['nama_mahasiswa']
        except jwt.ExpiredSignatureError:
            return jsonify({"status": "error", "message": "Token has expired"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"status": "error", "message": "Invalid token"}), 401
        
        
        
          
        query = """
        SELECT nama_mahasiswa, prodi_mahasiswa  , npm_mahasiswa , status_mahasiswa
        FROM dataset_mahasiswa WHERE npm_mahasiswa = %s
        """
        student_list = execute_query_with_retry(query , (npm_mahasiswa,))
        
        if not student_list:
            return jsonify({"success" : False , 'Message' : "Student is not in Student List"})
        
        student = student_list[0]
        
        if student:
            return jsonify({"success": True, "data": student})
        else:
            return jsonify({"success": False, "error": "Student not found."}), 404
    except mysql.connector.Error as e:
        logging.error(f"MySQL error: {e}")
        return jsonify({"status": False, "error": "Database error"}), 500
    except Exception as err:
        logging.error(f"Unexpected error: {err}")
        return jsonify({"status": False, "error": "Unexpected error occurred"}), 500
    
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
        
        
        # Query untuk mendapatkan data mahasiswa dan 3 mata kuliah pertama
        query_mahasiswa = """
        SELECT nama_mahasiswa, npm_mahasiswa, prodi_mahasiswa, angkatan_mahasiswa, status_mahasiswa , ipk_mahasiswa
        FROM dataset_mahasiswa 
        WHERE npm_mahasiswa = %s
        """
        
        student_list = execute_query_with_retry(query_mahasiswa , (npm_mahasiswa,))

        student = student_list[0]
        
        if not student:
            return jsonify({"success": False, "error": "Student not found."}), 404
        
        # Query untuk mengambil 3 mata kuliah mahasiswa
        query_mata_kuliah = """
        SELECT  SUM(sks_matakuliah) AS sks
        FROM dataset_krs 
        WHERE npm_mahasiswa = %s
        """
        
       
        mata_kuliah_list = execute_query_with_retry(query_mata_kuliah , (npm_mahasiswa,))  
        mata_kuliah = mata_kuliah_list[0]
        if mata_kuliah:
            return jsonify({
                "success": True,
                "data": {
                    "student": student,
                    "info": mata_kuliah
                }
            }), 200
        else:
            return jsonify({"success": False, "error": "No courses found for student."}), 404

    except mysql.connector.Error as e:
        logging.error(f"MySQL error: {e}")
        return jsonify({"status": False, "error": "Database error"}), 500
    except Exception as err:
        logging.error(f"Unexpected error: {err}")
        return jsonify({"status": False, "error": "Unexpected error occurred"}), 500
    
    

@app.route('/webinar')
def webinar_recommendations():
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

        

        
        # Ambil kategori yang terkait dari dataset_kegiatanmahasiswa dan dataset_krs
        query_categories = """
        SELECT DISTINCT kategori 
        FROM dataset_kegiatanmahasiswa 
        UNION 
        SELECT DISTINCT kategori_matakuliah 
        FROM dataset_krs WHERE npm_mahasiswa = %s
        """
        categories = execute_query_with_retry(query_categories , (npm_mahasiswa,))
        categories = [row['kategori'] for row in categories]
        
        if not categories:
            return jsonify({"error": "No categories found for this student."}), 404

        # Acak kategori
        random.shuffle(categories)
        
        recommended_webinars = []
        seen_kegiatan_names = set()  # Untuk menghindari duplikat nama kegiatan
        
        for kategori in categories:
            # Ambil webinar yang terkait dengan kategori saat ini (maksimal 2 per kategori)
            query_categories = ("""
                SELECT nama_kegiatan, kategori 
                FROM dataset_kegiatanmahasiswa 
                WHERE kategori = %s 
                ORDER BY RAND() 
                LIMIT 1
                
            """)
            webinars = execute_query_with_retry(query_categories , (kategori,))
            
            for webinar in webinars:
                nama_kegiatan = webinar['nama_kegiatan']
                
                # Pastikan tidak ada duplikat nama kegiatan
                if nama_kegiatan not in seen_kegiatan_names:
                    recommended_webinars.append({
                        "kategori": webinar['kategori'],
                        "nama_webinar": nama_kegiatan
                    })
                    seen_kegiatan_names.add(nama_kegiatan)
                
                # Hentikan jika kita sudah memiliki 6 webinar
                if len(recommended_webinars) >= 3:
                    break
            
            if len(recommended_webinars) >= 3:
                break

        if not recommended_webinars:
            return jsonify({"error": "No webinars found for the recommended categories."}), 404
        
        # Acak urutan webinar untuk memberikan keragaman lebih
        random.shuffle(recommended_webinars)
        
        return jsonify({
            "success": True,
            "npm_mahasiswa": npm_mahasiswa,
            "recommended_webinars": recommended_webinars
        }), 200
    
    except Error as e:
        logging.error(f"Database error: {e}")
        return jsonify({"status": False, "error": "Database error"}), 500
    except Exception as err:
        logging.error(f"Unexpected error: {err}")
        return jsonify({"status": False, "error": "Unexpected error occurred"}), 500


@app.route('/login', methods=['POST'])
def login():
    try:
        data = request.json
        npm_mahasiswa = data.get('npm_mahasiswa')
        password = data.get('password')

        if not npm_mahasiswa or not password:
            return jsonify({"status": "error", "message": "npm_mahasiswa and password are required"}), 400

        query = "SELECT * FROM dataset_mahasiswa WHERE npm_mahasiswa = %s"
        mhs_list = execute_query_with_retry(query , (npm_mahasiswa,))

        if not mhs_list:
            return jsonify({"status": "error", "message": "mhs not found"}), 404
        
        mhs = mhs_list[0]
        
        if password != mhs['npm_mahasiswa']:
            return jsonify({"status": "error", "message": "Invalid credentials"}), 401

        payload = {
            'npm_mahasiswa': mhs['npm_mahasiswa'],
            'nama_mahasiswa': mhs['nama_mahasiswa'],
            'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=1)
        }
        token = jwt.encode(payload, app.config['SECRET_KEY'], algorithm='HS256')

        session['npm_mahasiswa'] = mhs['npm_mahasiswa']
        session['nama_mahasiswa'] = mhs['nama_mahasiswa']

        return jsonify({
            "status": "success",
            "message": "Login successful",
            "npm_mahasiswa": mhs['npm_mahasiswa'],
            "nama_mahasiswa": mhs['nama_mahasiswa'],
            "token": token
        })

    except Exception as e:
        logging.error(f"Login error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({"status": "success", "message": "Logout successful"})

if __name__ == '__main__':
    app.run(debug=True)
