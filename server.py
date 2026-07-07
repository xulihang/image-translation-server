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


@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    response.headers['Access-Control-Allow-Private-Network'] = 'true'
    return response

# Configuration
BASE_DIR = Path(__file__).parent
TEMPLATES_DIR = BASE_DIR / 'templates'
TEMP_DIR = BASE_DIR / 'temp'  # 临时任务目录
IMAGETRANS_DIR = BASE_DIR / 'ImageTrans'

TASKS_FILE = BASE_DIR / 'tasks.json'
MAX_CONCURRENT_TASKS = 3
TASK_CLEANUP_DELAY = 1800  # 30 minutes in seconds

# Ensure directories exist
TEMPLATES_DIR.mkdir(exist_ok=True)
TEMP_DIR.mkdir(exist_ok=True)
IMAGETRANS_DIR.mkdir(exist_ok=True)

# Task management
tasks = {}
task_lock = threading.Lock()
available_workers = list(range(3))
worker_lock = threading.Lock()
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


def process_image_trans(task_id, template_name, settings_json, preferences_conf, ocr_based_on_lang, headless=False):
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
        imagetrans_dir = IMAGETRANS_DIR
        
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

        # Ensure java has execute permission (zip extraction may lose it on Linux)
        import stat
        java_path.chmod(java_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        
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
        ]

        if headless:
            cmd.extend([
                '-Dglass.platform=headless',
            ])

        cmd += [
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
        
        # 获取输出和错误信息（1分钟超时）
        try:
            stdout, stderr = process.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            print(f"[TIMEOUT] Task {task_id} timed out after 60s, marking as completed")

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


def map_template_name(ws_template):
    """Map wsServer template names to available templates"""
    if not ws_template:
        ws_template = 'general'

    template_dir = TEMPLATES_DIR / ws_template
    if template_dir.exists():
        return ws_template

    mapping = {
        'general': 'comics',
        'manga': 'manga-ja2zh',
        'cg': 'comics',
        'webtoon': 'comics',
        'chinese-manhua': 'comics',
        'document': 'comics',
    }

    mapped = mapping.get(ws_template)
    if mapped and (TEMPLATES_DIR / mapped).exists():
        return mapped

    for d in TEMPLATES_DIR.iterdir():
        if d.is_dir():
            return d.name

    return 'comics'


def convert_to_jpg(image_bytes):
    """Convert any image format bytes to JPEG bytes using Pillow."""
    from io import BytesIO
    from PIL import Image
    img = Image.open(BytesIO(image_bytes))
    if img.mode in ('RGBA', 'P', 'LA'):
        img = img.convert('RGB')
    buf = BytesIO()
    img.save(buf, format='JPEG', quality=92)
    return buf.getvalue()


def convert_to_webp_base64(filepath):
    """Read an image file, convert to WebP, return base64 string."""
    import base64
    from io import BytesIO
    from PIL import Image
    img = Image.open(filepath)
    if img.mode in ('RGBA', 'P', 'LA'):
        img = img.convert('RGB')
    buf = BytesIO()
    img.save(buf, format='WEBP', quality=85)
    return base64.b64encode(buf.getvalue()).decode('utf-8')


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
            headless:
              type: boolean
              description: Whether to run JavaFX in headless mode
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

        # Save image to project directory immediately (don't store in tasks.json)
        task_dir = TEMP_DIR / task_id
        project_dir = task_dir / 'project'
        project_dir.mkdir(parents=True, exist_ok=True)
        import base64
        image_bytes = base64.b64decode(data['image_base64'])
        with open(project_dir / '0.jpg', 'wb') as f:
            f.write(image_bytes)

        # Store task (without image data)
        with task_lock:
            tasks[task_id] = {
                'status': 'queued',
                'template_name': template_name,
                'settings_json': data.get('settings_json'),
                'preferences_json': data.get('preferences_json'),
                'ocr_based_on_lang': data.get('ocr_based_on_lang', False),
                'headless': data.get('headless', False),
                'created_time': datetime.now().isoformat(),
                'work_dir': str(TEMP_DIR / task_id)
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
                data.get('ocr_based_on_lang', False),
                data.get('headless', False)
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
        project_dir = TEMP_DIR / task_id / 'project'
        for image_name in results:
            # Look for translated image (might have different naming conventions)
            img_file = project_dir / 'out' / image_name
            if img_file.exists():
                import base64
                with open(img_file, 'rb') as f:
                    img_data = base64.b64encode(f.read()).decode('utf-8')
                results[image_name]['translated'] = img_data
    
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


@app.route('/translate', methods=['POST'])
def translate_compatible():
    """
    Compatible endpoint matching ImageTrans_wsServer's /translate.
    Accepts application/x-www-form-urlencoded, returns JSON synchronously.
    """
    import base64

    src = request.form.get('src', '')
    source_lang = request.form.get('sourceLang', '')
    target_lang = request.form.get('targetLang', '')
    template = request.form.get('template', 'general')
    project_settings = request.form.get('projectSettings', '')
    apis = request.form.get('apis', '')
    without_image = request.form.get('withoutImage', 'false').lower() == 'true'
    response_type = request.form.get('type', '')
    callback = request.form.get('callback', '')
    headless = request.form.get('headless', 'false').lower() == 'true'

    if not src:
        resp = {'success': False, 'message': 'src is required'}
        if callback:
            return f'{callback}({json.dumps(resp)})', 200, {'Content-Type': 'application/javascript'}
        return jsonify(resp), 400

    # Extract base64 from data URI or raw base64
    if ',' in src and src.startswith('data:'):
        image_base64 = src.split(',', 1)[1]
    else:
        image_base64 = src

    try:
        image_bytes = base64.b64decode(image_base64)
    except Exception:
        resp = {'success': False, 'message': 'Invalid base64 image data'}
        if callback:
            return f'{callback}({json.dumps(resp)})', 200, {'Content-Type': 'application/javascript'}
        return jsonify(resp), 400

    # Map template name
    template_name = map_template_name(template)

    # Build settings from wsServer params
    settings = {}
    if project_settings:
        try:
            settings.update(json.loads(project_settings))
        except json.JSONDecodeError:
            pass
    if source_lang:
        settings['sourceLang'] = source_lang
    if target_lang:
        settings['targetLang'] = target_lang
    if apis:
        try:
            settings.update(json.loads(apis))
        except json.JSONDecodeError:
            pass

    settings_json = json.dumps(settings) if settings else None

    # Create task
    task_id = str(uuid.uuid4())

    task_dir = TEMP_DIR / task_id
    project_dir = task_dir / 'project'
    project_dir.mkdir(parents=True, exist_ok=True)

    # Convert to JPG for ImageTrans compatibility
    try:
        image_bytes = convert_to_jpg(image_bytes)
    except Exception:
        pass  # fall through with original bytes if conversion fails

    with open(project_dir / '0.jpg', 'wb') as f:
        f.write(image_bytes)

    with task_lock:
        tasks[task_id] = {
            'status': 'queued',
            'template_name': template_name,
            'settings_json': settings_json,
            'preferences_json': None,
            'ocr_based_on_lang': True,
            'headless': headless,
            'created_time': datetime.now().isoformat(),
            'work_dir': str(TEMP_DIR / task_id)
        }
        save_tasks()

    thread = threading.Thread(
        target=process_image_trans,
        args=(task_id, template_name, settings_json, None, True, headless)
    )
    thread.daemon = True
    thread.start()

    # Wait for completion (synchronous, matching wsServer behavior)
    timeout = 240
    elapsed = 0
    while elapsed < timeout:
        time.sleep(1)
        elapsed += 1

        with task_lock:
            task = tasks.get(task_id)

        if not task:
            break

        if task['status'] == 'completed':
            results = read_itp_file(task_id)
            if not results:
                resp = {'success': False, 'message': 'Results not available'}
                if callback:
                    return f'{callback}({json.dumps(resp)})', 200, {'Content-Type': 'application/javascript'}
                return jsonify(resp)

            boxes = []
            for image_name, image_data in results.items():
                for box in image_data.get('boxes', []):
                    entry = {
                        'text': box.get('text', ''),
                        'target': box.get('target', ''),
                        'geometry': box.get('geometry', {})
                    }
                    if 'targetGeometry' in box:
                        entry['targetGeometry'] = box['targetGeometry']
                    else:
                        entry['targetGeometry'] = box.get('geometry', {})
                    boxes.append(entry)

            resp = {'success': True, 'imgMap': {'boxes': boxes}}

            if not without_image:
                out_dir = TEMP_DIR / task_id / 'project' / 'out'
                if out_dir.exists():
                    for image_name in results:
                        img_file = out_dir / image_name
                        if img_file.exists():
                            resp['img'] = convert_to_webp_base64(img_file)
                            break

            if response_type == 'html' and 'img' in resp:
                html = f'<img src="data:image/webp;base64,{resp["img"]}">'
                if callback:
                    return f'{callback}({json.dumps({"html": html})})', 200, {'Content-Type': 'application/javascript'}
                return html

            if callback:
                return f'{callback}({json.dumps(resp)})', 200, {'Content-Type': 'application/javascript'}
            return jsonify(resp)

        elif task['status'] == 'failed':
            error = task.get('error', 'Translation failed')
            resp = {'success': False, 'message': error}
            if callback:
                return f'{callback}({json.dumps(resp)})', 200, {'Content-Type': 'application/javascript'}
            return jsonify(resp)

    # Timeout
    resp = {'success': False, 'message': 'timeout'}
    if callback:
        return f'{callback}({json.dumps(resp)})', 200, {'Content-Type': 'application/javascript'}
    return jsonify(resp)


@app.route('/translateRegion', methods=['POST'])
def translate_region_compatible():
    """
    Compatible endpoint matching ImageTrans_wsServer's /translateRegion.
    OCR and translate a single image region.
    """
    import base64

    image_b64 = request.form.get('base64', '')
    source_lang = request.form.get('sourceLang', '')
    target_lang = request.form.get('targetLang', '')
    callback = request.form.get('callback', '')

    if not image_b64:
        resp = {'success': False, 'message': 'base64 is required'}
        if callback:
            return f'{callback}({json.dumps(resp)})', 200, {'Content-Type': 'application/javascript'}
        return jsonify(resp)

    # Strip data URI prefix if present
    if ',' in image_b64 and image_b64.startswith('data:'):
        image_b64 = image_b64.split(',', 1)[1]

    try:
        image_bytes = base64.b64decode(image_b64)
    except Exception:
        resp = {'success': False, 'message': 'Invalid base64 image data'}
        if callback:
            return f'{callback}({json.dumps(resp)})', 200, {'Content-Type': 'application/javascript'}
        return jsonify(resp), 400

    # Use a minimal template and create a task for region detection + translation
    template_name = map_template_name('general')

    settings = {}
    if source_lang:
        settings['sourceLang'] = source_lang
    if target_lang:
        settings['targetLang'] = target_lang
    settings_json = json.dumps(settings) if settings else None

    task_id = str(uuid.uuid4())

    task_dir = TEMP_DIR / task_id
    project_dir = task_dir / 'project'
    project_dir.mkdir(parents=True, exist_ok=True)

    try:
        image_bytes = convert_to_jpg(image_bytes)
    except Exception:
        pass

    with open(project_dir / '0.jpg', 'wb') as f:
        f.write(image_bytes)

    with task_lock:
        tasks[task_id] = {
            'status': 'queued',
            'template_name': template_name,
            'settings_json': settings_json,
            'preferences_json': None,
            'ocr_based_on_lang': False,
            'headless': False,
            'created_time': datetime.now().isoformat(),
            'work_dir': str(TEMP_DIR / task_id)
        }
        save_tasks()

    thread = threading.Thread(
        target=process_image_trans,
        args=(task_id, template_name, settings_json, None, False, False)
    )
    thread.daemon = True
    thread.start()

    timeout = 240
    elapsed = 0
    while elapsed < timeout:
        time.sleep(1)
        elapsed += 1

        with task_lock:
            task = tasks.get(task_id)

        if not task:
            break

        if task['status'] == 'completed':
            results = read_itp_file(task_id)
            if not results:
                resp = {'success': False, 'message': 'Results not available'}
                if callback:
                    return f'{callback}({json.dumps(resp)})', 200, {'Content-Type': 'application/javascript'}
                return jsonify(resp)

            # Build regionMap from first image's first box
            region_map = {'source': '', 'target': []}
            for image_data in results.values():
                boxes = image_data.get('boxes', [])
                if boxes:
                    first_box = boxes[0]
                    region_map['source'] = first_box.get('text', '')
                    target_text = first_box.get('target', '')
                    region_map['target'] = [{
                        'engine': 'imagetrans',
                        'text': target_text
                    }]
                break

            resp = {'success': True, 'regionMap': region_map}
            if callback:
                return f'{callback}({json.dumps(resp)})', 200, {'Content-Type': 'application/javascript'}
            return jsonify(resp)

        elif task['status'] == 'failed':
            error = task.get('error', 'Translation failed')
            resp = {'success': False, 'message': error}
            if callback:
                return f'{callback}({json.dumps(resp)})', 200, {'Content-Type': 'application/javascript'}
            return jsonify(resp)

    resp = {'success': False, 'message': 'timeout'}
    if callback:
        return f'{callback}({json.dumps(resp)})', 200, {'Content-Type': 'application/javascript'}
    return jsonify(resp)


@app.route('/list', methods=['GET'])
def list_instances():
    """
    Compatible endpoint matching ImageTrans_wsServer's /list.
    Returns connected instances as a JSON array.
    """
    with worker_lock:
        free_count = len(available_workers)
        running = free_count < MAX_CONCURRENT_TASKS

    return jsonify([{
        'running': running,
        'displayName': 'default',
        'name': 'imagetrans_server'
    }])


@app.route('/api/upload-imagetrans', methods=['POST'])
def upload_imagetrans():
    """
    Upload and install ImageTrans from a zip file.
    Extracts directly to the ImageTrans directory.
    ---
    tags:
      - Administration
    parameters:
      - in: formData
        name: file
        type: file
        required: true
        description: imagetrans.zip containing the ImageTrans installation
    responses:
      200:
        description: Installation successful
      400:
        description: Invalid request or missing file
      500:
        description: Server error during extraction
    """
    import zipfile

    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    if not file.filename.lower().endswith('.zip'):
        return jsonify({'error': 'File must be a .zip file'}), 400

    temp_zip = BASE_DIR / '_imagetrans_upload.zip'

    try:
        file.save(str(temp_zip))

        if not zipfile.is_zipfile(temp_zip):
            temp_zip.unlink()
            return jsonify({'error': 'Uploaded file is not a valid zip archive'}), 400

        # Remove old installation
        if IMAGETRANS_DIR.exists():
            shutil.rmtree(IMAGETRANS_DIR, ignore_errors=True)

        # Extract directly to ImageTrans directory
        IMAGETRANS_DIR.mkdir(parents=True)
        with zipfile.ZipFile(temp_zip, 'r') as zf:
            zf.extractall(str(IMAGETRANS_DIR))

        # Fix executable permissions lost during zip extraction (Linux)
        java_bin_dir = IMAGETRANS_DIR / 'jre' / 'bin'
        if java_bin_dir.exists():
            import stat
            for f in java_bin_dir.iterdir():
                if f.is_file():
                    f.chmod(f.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        temp_zip.unlink()

        return jsonify({
            'success': True,
            'message': 'ImageTrans installed successfully'
        })

    except zipfile.BadZipFile:
        if temp_zip.exists():
            temp_zip.unlink()
        return jsonify({'error': 'Invalid or corrupted zip file'}), 400
    except Exception as e:
        if temp_zip.exists():
            temp_zip.unlink()
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)