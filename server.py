# app.py
import os
import json
import uuid
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory
from flask_swagger_ui import get_swaggerui_blueprint
from werkzeug.utils import secure_filename

app = Flask(__name__)

# Configuration
BASE_DIR = Path(__file__).parent
TEMPLATES_DIR = BASE_DIR / 'templates'
TEMP_DIR = BASE_DIR / 'temp'  # 临时任务目录
IMAGETRANS_DIRS = [
    BASE_DIR / 'ImageTrans1',
    BASE_DIR / 'ImageTrans2',
    BASE_DIR / 'ImageTrans3'
]
TASKS_FILE = BASE_DIR / 'tasks.json'
MAX_CONCURRENT_TASKS = 3
TASK_CLEANUP_DELAY = 1800  # 30 minutes in seconds

# Ensure directories exist
TEMPLATES_DIR.mkdir(exist_ok=True)
TEMP_DIR.mkdir(exist_ok=True)
for d in IMAGETRANS_DIRS:
    d.mkdir(exist_ok=True)

# Task management
tasks = {}
task_lock = threading.Lock()
available_workers = list(range(3))  # 0-2 for ImageTrans1-3
worker_lock = threading.Lock()

# Load existing tasks if any
if TASKS_FILE.exists():
    with open(TASKS_FILE, 'r') as f:
        tasks = json.load(f)


def save_tasks():
    """Save tasks to file"""
    with open(TASKS_FILE, 'w') as f:
        json.dump(tasks, f, indent=2)


def cleanup_task(task_id):
    """Clean up task after delay"""
    time.sleep(TASK_CLEANUP_DELAY)
    with task_lock:
        if task_id in tasks:
            task_dir = Path(tasks[task_id]['work_dir'])
            if task_dir.exists():
                shutil.rmtree(task_dir)
            del tasks[task_id]
            save_tasks()


def process_image_trans(task_id, template_name, settings_json, preferences_conf, ocr_based_on_lang):
    """Process image translation in a worker thread"""
    worker_id = None
    
    try:
        # Get available worker
        while worker_id is None:
            with worker_lock:
                if available_workers:
                    worker_id = available_workers.pop(0)
            if worker_id is None:
                time.sleep(1)
        
        # Set up directories
        task_dir = TEMP_DIR / task_id
        project_dir = task_dir / 'project'
        project_dir.mkdir(parents=True, exist_ok=True)
        
        # Copy template
        template_dir = TEMPLATES_DIR / template_name
        if template_dir.exists():
            shutil.copytree(template_dir, task_dir, dirs_exist_ok=True)
        
        # Copy image to project directory
        with task_lock:
            image_data = tasks[task_id]['image_base64']
        
        import base64
        image_bytes = base64.b64decode(image_data)
        with open(project_dir / '0.jpg', 'wb') as f:
            f.write(image_bytes)
        
        # Handle OCR based on language setting
        if ocr_based_on_lang:
            (task_dir / 'setOCRBasedOnLang').touch()
        
        # Write settings if provided
        if settings_json:
            try:
                settings = json.loads(settings_json)
                with open(task_dir / 'settings.json', 'w', encoding='utf-8') as f:
                    json.dump(settings, f, ensure_ascii=False, indent=2)
            except json.JSONDecodeError:
                pass
        
        # Write preferences if provided
        if preferences_conf:
            with open(task_dir / 'preferences.conf', 'w', encoding='utf-8') as f:
                f.write(preferences_conf)
        
        # Update task status
        with task_lock:
            tasks[task_id]['status'] = 'processing'
            tasks[task_id]['worker'] = worker_id
            save_tasks()
        
        # Execute ImageTrans command
        imagetrans_dir = IMAGETRANS_DIRS[worker_id]
        
        # 检查 Java 可执行文件是否存在
        java_path = imagetrans_dir / 'jre' / 'bin' / 'java'
        if not java_path.exists():
            # Windows 上尝试添加 .exe 后缀
            java_path = imagetrans_dir / 'jre' / 'bin' / 'java.exe'
        
        if not java_path.exists():
            error_msg = f"Java executable not found at {java_path}. Please check ImageTrans installation."
            print(f"[ERROR] {error_msg}")
            with task_lock:
                tasks[task_id]['status'] = 'failed'
                tasks[task_id]['error'] = error_msg
                save_tasks()
            return
        
        # 检查 ImageTrans.jar 是否存在
        jar_path = imagetrans_dir / 'ImageTrans.jar'
        if not jar_path.exists():
            error_msg = f"ImageTrans.jar not found at {jar_path}"
            print(f"[ERROR] {error_msg}")
            with task_lock:
                tasks[task_id]['status'] = 'failed'
                tasks[task_id]['error'] = error_msg
                save_tasks()
            return
        
        # 使用绝对路径构建命令
        cmd = [
            str(java_path),
            '-Xmx2048M',
            '--module-path', str(imagetrans_dir / 'jre' / 'javafx' / 'lib'),
            '--add-modules', 'javafx.base,javafx.controls,javafx.graphics,javafx.web,javafx.swing',
            '--add-opens', 'javafx.controls/com.sun.javafx.scene.control.skin=ALL-UNNAMED',
            '--add-exports', 'javafx.base/com.sun.javafx.collections=ALL-UNNAMED',
            '--add-exports', 'java.desktop/sun.awt=ALL-UNNAMED',
            '--add-exports', 'java.desktop/com.sun.imageio.plugins.jpeg=ALL-UNNAMED',
            '--add-exports', 'java.desktop/com.sun.imageio.plugins.png=ALL-UNNAMED',
            '--add-exports', 'java.desktop/com.sun.imageio.plugins.bmp=ALL-UNNAMED',
            '--add-exports', 'java.desktop/com.sun.imageio.plugins.gif=ALL-UNNAMED',
            '--add-exports', 'java.desktop/com.sun.imageio.plugins.wbmp=ALL-UNNAMED',
            '--add-exports', 'java.desktop/com.sun.imageio.spi=ALL-UNNAMED',
            '--add-opens', 'java.desktop/com.sun.imageio.plugins.jpeg=ALL-UNNAMED',
            '-jar', str(jar_path),
            str(task_dir),
            str(project_dir)
        ]
        
        print(f"[DEBUG] Running command: {' '.join(cmd)}")
        print(f"[DEBUG] Working directory: {imagetrans_dir}")
        
        # Run the command with better error handling
        process = subprocess.Popen(
            cmd,
            cwd=str(imagetrans_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True  # 以文本模式返回输出
        )
        
        # 获取输出和错误信息
        stdout, stderr = process.communicate()
        
        # 打印输出信息用于调试
        if stdout:
            print(f"[STDOUT] {stdout}")
        if stderr:
            print(f"[STDERR] {stderr}")
        
        # Update task status
        with task_lock:
            if process.returncode == 0:
                tasks[task_id]['status'] = 'completed'
                tasks[task_id]['completed_time'] = datetime.now().isoformat()
                print(f"[INFO] Task {task_id} completed successfully")
            else:
                error_details = f'Process exited with code {process.returncode}\n'
                if stderr:
                    error_details += f'Error output: {stderr}\n'
                if stdout:
                    error_details += f'Standard output: {stdout}'
                tasks[task_id]['status'] = 'failed'
                tasks[task_id]['error'] = error_details
                print(f"[ERROR] Task {task_id} failed: {error_details}")
            save_tasks()
        
        # Schedule cleanup only if completed
        if tasks[task_id]['status'] == 'completed':
            cleanup_thread = threading.Thread(target=cleanup_task, args=(task_id,))
            cleanup_thread.daemon = True
            cleanup_thread.start()
    
    except Exception as e:
        error_msg = f"Exception in process: {str(e)}"
        print(f"[EXCEPTION] Task {task_id}: {error_msg}")
        import traceback
        traceback.print_exc()
        
        with task_lock:
            tasks[task_id]['status'] = 'failed'
            tasks[task_id]['error'] = error_msg
            save_tasks()
    
    finally:
        # Release worker
        if worker_id is not None:
            with worker_lock:
                available_workers.append(worker_id)
                print(f"[DEBUG] Released worker {worker_id}")


def read_itp_file(task_id):
    """Read ITP file and extract translation results"""
    task_dir = TEMP_DIR / task_id
    itp_file = task_dir / 'project' / '1.itp'
    
    if not itp_file.exists():
        return None
    
    with open(itp_file, 'r', encoding='utf-8') as f:
        itp_data = json.load(f)
    
    results = {}
    for image_name, image_data in itp_data.get('images', {}).items():
        boxes = []
        for box in image_data.get('boxes', []):
            boxes.append({
                'text': box.get('text', ''),
                'target': box.get('target', ''),
                'geometry': box.get('geometry', {})
            })
        
        results[image_name] = {
            'boxes': boxes
        }
    
    return results


# Swagger UI setup
SWAGGER_URL = '/api/docs'
API_URL = '/static/swagger.json'
swaggerui_blueprint = get_swaggerui_blueprint(
    SWAGGER_URL,
    API_URL,
    config={
        'app_name': "Image Translation API"
    }
)
app.register_blueprint(swaggerui_blueprint, url_prefix=SWAGGER_URL)


@app.route('/api/templates', methods=['GET'])
def get_templates():
    """
    Get available templates
    ---
    tags:
      - Templates
    responses:
      200:
        description: List of available templates
        schema:
          type: object
          properties:
            templates:
              type: array
              items:
                type: string
    """
    templates = []
    if TEMPLATES_DIR.exists():
        templates = [d.name for d in TEMPLATES_DIR.iterdir() if d.is_dir()]
    return jsonify({'templates': templates})


@app.route('/api/translate', methods=['POST'])
def create_translation_task():
    """
    Create a new translation task
    ---
    tags:
      - Translation
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - image_base64
            - template_name
          properties:
            image_base64:
              type: string
              description: Base64 encoded image
            template_name:
              type: string
              description: Template name to use
            settings_json:
              type: string
              description: Additional project settings in JSON format
            preferences_json:
              type: string
              description: Additional preferences in JSON format
            ocr_based_on_lang:
              type: boolean
              description: Whether to select OCR based on project language
              default: false
    responses:
      200:
        description: Task created successfully
        schema:
          type: object
          properties:
            task_id:
              type: string
            message:
              type: string
      400:
        description: Invalid request
    """
    try:
        data = request.get_json()
        
        if not data.get('image_base64'):
            return jsonify({'error': 'image_base64 is required'}), 400
        
        if not data.get('template_name'):
            return jsonify({'error': 'template_name is required'}), 400
        
        # Validate template exists
        template_name = data['template_name']
        template_dir = TEMPLATES_DIR / template_name
        if not template_dir.exists():
            return jsonify({'error': f'Template {template_name} not found'}), 400
        
        # Generate task ID
        task_id = str(uuid.uuid4())
        
        # Store task
        with task_lock:
            tasks[task_id] = {
                'status': 'queued',
                'template_name': template_name,
                'image_base64': data['image_base64'],
                'settings_json': data.get('settings_json'),
                'preferences_json': data.get('preferences_json'),
                'ocr_based_on_lang': data.get('ocr_based_on_lang', False),
                'created_time': datetime.now().isoformat(),
                'work_dir': str(TEMPLATES_DIR / task_id)
            }
            save_tasks()
        
        # Start processing in background
        thread = threading.Thread(
            target=process_image_trans,
            args=(
                task_id,
                template_name,
                data.get('settings_json'),
                data.get('preferences_json'),
                data.get('ocr_based_on_lang', False)
            )
        )
        thread.daemon = True
        thread.start()
        
        return jsonify({
            'task_id': task_id,
            'message': 'Task created successfully'
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/translate/<task_id>/status', methods=['GET'])
def get_task_status(task_id):
    """
    Get translation task status
    ---
    tags:
      - Translation
    parameters:
      - in: path
        name: task_id
        type: string
        required: true
        description: Task ID
    responses:
      200:
        description: Task status
        schema:
          type: object
          properties:
            task_id:
              type: string
            status:
              type: string
              enum: [queued, processing, completed, failed]
            completed:
              type: boolean
      404:
        description: Task not found
    """
    with task_lock:
        task = tasks.get(task_id)
    
    if not task:
        return jsonify({'error': 'Task not found'}), 404
    
    return jsonify({
        'task_id': task_id,
        'status': task['status'],
        'completed': task['status'] == 'completed'
    })


@app.route('/api/translate/<task_id>/result', methods=['GET'])
def get_translation_result(task_id):
    """
    Get translation results
    ---
    tags:
      - Translation
    parameters:
      - in: path
        name: task_id
        type: string
        required: true
        description: Task ID
      - in: query
        name: include_base64
        type: boolean
        required: false
        description: Whether to include base64 encoded translated image
        default: false
    responses:
      200:
        description: Translation results
        schema:
          type: object
          properties:
            task_id:
              type: string
            status:
              type: string
            results:
              type: object
      404:
        description: Task not found
      400:
        description: Task not completed yet
    """
    with task_lock:
        task = tasks.get(task_id)
    
    if not task:
        return jsonify({'error': 'Task not found'}), 404
    
    if task['status'] != 'completed':
        return jsonify({'error': 'Task not completed yet', 'status': task['status']}), 400
    
    # Read ITP results
    results = read_itp_file(task_id)
    if not results:
        return jsonify({'error': 'Results not available'}), 500
    
    # Optionally include translated image as base64
    include_base64 = request.args.get('include_base64', 'false').lower() == 'true'
    
    if include_base64:
        project_dir = TEMPLATES_DIR / task_id / 'project'
        for image_name in results:
            # Look for translated image (might have different naming conventions)
            for ext in ['.jpg', '.png', '.webp']:
                img_file = project_dir / f'translated_{image_name}{ext}'
                if not img_file.exists():
                    img_file = project_dir / image_name
                
                if img_file.exists():
                    import base64
                    with open(img_file, 'rb') as f:
                        img_data = base64.b64encode(f.read()).decode('utf-8')
                    results[image_name]['translated'] = img_data
                    break
    
    return jsonify({
        'task_id': task_id,
        'status': task['status'],
        'results': results
    })


@app.route('/api/tasks', methods=['GET'])
def list_tasks():
    """
    List all tasks
    ---
    tags:
      - Translation
    responses:
      200:
        description: List of tasks
    """
    with task_lock:
        task_list = [
            {
                'task_id': tid,
                'status': t['status'],
                'template_name': t.get('template_name'),
                'created_time': t.get('created_time')
            }
            for tid, t in tasks.items()
        ]
    
    return jsonify({'tasks': task_list})

@app.route('/')
def index():
    """Serve the test page"""
    return send_from_directory('templates', 'index.html')
    
@app.route('/static/swagger.json')
def swagger_json():
    """Serve Swagger JSON"""
    return send_from_directory('.', 'swagger.json')


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)