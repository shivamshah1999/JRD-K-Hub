"""
blueprint app.py example implementation from https://realpython.com/flask-blueprint/
---
from flask import Flask
from example_blueprint import example_blueprint

app = Flask(__name__)
app.register_blueprint(example_blueprint)

"""

# Flask imports
from flask import Flask, flash, get_flashed_messages, render_template, request, redirect, url_for, session, make_response, abort, send_from_directory, send_file

# Firebase imports
import firebase_admin
from firebase_admin import credentials, firestore, auth
from firebase_admin.auth import UserRecord
import google.auth.credentials

# Other installed modules imports
from werkzeug.utils import secure_filename
import mock

# Built-in modules imports
import os, json, sys, requests, uuid, time
from datetime import datetime

# Local imports
from story_editing.TwineIngestFirestore import firestoreTwineConvert
from utils import url, db, render_response
from users import User, UserActivity, current_user, login_user, login_required, admin_login_required, user_blueprint
from errors import errors_blueprint
from editor_blueprint import editor_blueprint




# Checks which platform we are running on
platform = os.environ.get('PLATFORM', 'local')

if platform == 'prod':
    static_folder = ''

if platform == 'local':
    static_folder = '../../static'

# Initialize the Flask application
app = Flask(__name__, template_folder='pages', static_folder=static_folder)

# Sets the path for the file upload folder
app.config['UPLOAD_FOLDER'] = 'file_uploads'

# Sets the allowed extensions for file uploads
app.config['ALLOWED_EXTENSIONS'] = ['html', 'jpeg', 'mp3', 'mp4', 'pdf', 'png', 'svg', 'tgif', 'txt']

# blueprints
app.register_blueprint(user_blueprint)
app.register_blueprint(errors_blueprint)
app.register_blueprint(editor_blueprint)


@app.route('/')
def index():
    """index()
    Serves the home page
    Accessed at '/' via a GET request
    """

    # Checks if the user is logged in
    if current_user:
        # Checks if the user is an admin
        if current_user.admin:
            # Returns the admin homepage
            return render_response(render_template('admin_pages/homepage.html', first_name=current_user.first_name))

        # Gets the active engine to link to with the 'Begin Story' button
        begin_story = db.collection('application_states').document('application_state').get().get('active_story_id')

        # Gets the most recent story to link to with the 'Continue Story' button
        most_recent_history = None
        continue_story = None
        # Loops over the stories in the user's history
        for index, history in enumerate(current_user.history):
            # Sets the continue_story to the first story in the user's history
            if most_recent_history is None:
                most_recent_history = index
                continue_story = current_user.history[most_recent_history]['story'] + '/' + current_user.history[most_recent_history]['pages'][-1]

            # Checks if each story was more recently accessed than the current_story; if so, updates the current_story
            elif history['last_updated'].replace(tzinfo=None) > current_user.history[most_recent_history]['last_updated'].replace(tzinfo=None):
                most_recent_history = index
                continue_story = current_user.history[most_recent_history]['story'] + '/' + current_user.history[most_recent_history]['pages'][-1]

        # Returns the user homepage
        return render_response(render_template('user_pages/homepage.html', first_name=current_user.first_name, begin_story=begin_story, continue_story=continue_story, history=most_recent_history))

    # Returns the homepage
    return render_response(render_template('home.html'))


# Defines which attributes of a User can be used in the page contents
# $$attr$$ is replaced with current_user.attr
allowed_user_attr = ['first_name', 'last_name', 'email']

# Serves the root page of the specified story
@app.route('/story/<story>')
def story_root(story):
    """story_root()
    Serves the root page of a story
    Accessed at '/story/<story' via a GET request
    """

    # Gets the DocumentReference to the story document in Firestore
    story_ref = db.collection('stories').document(story)

    # Gets the DocumentSnapshot of the story document in Firestore
    story_doc = story_ref.get()

    # Checks whether or not the story exists
    if not story_doc.exists:
        abort(404)

    # Gets the root page's page ID
    page_id = story_doc.get('root_id')

    # Gets the page data for the specified page ID
    page = story_doc.get('page_nodes.`' + page_id + '`')

    # Gets whether or not the page is viewed as a preview (from history page)
    preview = request.args.get('preview')
    if preview == None:
        preview = False

    # Gets whether or not the user is logged in
    guest = current_user == None

    # Replaces user attributes in the page content with the current user's values
    for user_attr in allowed_user_attr:
        page['page_body_text'] = page['page_body_text'].replace('$$' + user_attr + '$$', 'Guest' if guest else getattr(current_user, user_attr))

    history_id = None
    if not preview and not guest:
        # Records the page visit to story activity
        user_activity = UserActivity.get_user_activity(current_user.email)
        user_activity.story_activity.append({
            'timestamp': datetime.now(),
            'story': story,
            'page_id': page_id
            })
        user_activity.save()

        # Checks for a matching history, to not add a duplicate history
        history_found = False
        for index, history in enumerate(current_user.history):
            if history['story'] == story and history['pages'][0] == page_id and len(history['pages']) == 1:
                # Updates timestamp of matching history
                history['last_updated'] = datetime.now()
                history_found = True
                history_id = index

        # If a matching history does not already exists, adds the root page to a new history
        if not history_found:
            new_history = {}
            new_history['last_updated'] = datetime.now()
            new_history['story'] = story
            new_history['pages'] = [page_id]
            current_user.history.append(new_history)
            history_id = len(current_user.history) - 1

        # Saves the changes to the user
        current_user.save()

    # Gets whether or not the page is favorited
    favorited = False
    if not guest:
        for favorite in current_user.favorites:
            if favorite['story'] == story and favorite['page_id'] == page_id:
                favorited = True

    # Returns the story_page.html template with the specified page
    return render_response(render_template('story_page.html', guest=guest, favorited=favorited, story=story, page=page, preview=preview, history=history_id))


# Serves the specified page of the specified story
@app.route('/story/<story>/<page_id>', methods=['GET', 'POST'])
def story_page(story, page_id):
    """story_page()
    Serves a page of a story
    Accessed at '/story/<story/<page_id>' via a GET or POST request
    """

    # Gets the DocumentReference to the story document in Firestore
    story_ref = db.collection('stories').document(story)

    # Gets the DocumentSnapshot of the story document in Firestore
    story_doc = story_ref.get()

    # Checks whether or not the story exists
    if not story_doc.exists:
        abort(404)

    # Checks whether or not the page exists in the story
    if page_id not in story_doc.get('page_nodes'):
        abort(404)

    # Gets the page data for the specified page ID
    page = story_doc.get('page_nodes.`' + page_id + '`')

    # Gets whether or not the page should be displayed as a preview
    preview = request.args.get('preview')
    if preview == None:
        preview = False

    # Gets whether or not the user is a guest
    guest = current_user == None

    # Replaces user attributes in the page content with the current user's values
    for user_attr in allowed_user_attr:
        page['page_body_text'] = page['page_body_text'].replace('$$' + user_attr + '$$', 'Guest' if guest else getattr(current_user, user_attr))

    # Gets whether or not the page is favorited
    favorited = False
    if not guest:
        for favorite in current_user.favorites:
            if favorite['story'] == story and favorite['page_id'] == page_id:
                favorited = True

    ###############################################################################################################
    # In order to log a user's history, each time they click a link when navigating a story we include the        #
    # previous page, an ID for which history they are on (so if they have taken multiple paths through the        #
    # story, which index in the database corresponds to the current one), and whether or not they are navigating  #
    # forward or backwards. If a request to a story page comes from one of the ways we intend (homepage, history, #
    # favorites, another story page), then it will be formed as a POST request with these fields so that we know  #
    # exactly what the user is doing. If the user reaches the page through another means (most likely copying and #
    # pasting the URL, i.e. a GET request), then we will treat this as the user starting a new history from       #
    # whichever page they go to, since we don't have a history to tie this to.                                    #
    ###############################################################################################################

    # Checks that the request comes as a POST request and includes the information for recording user history
    if request.method == 'POST':
        prev_page_id = request.form['prev_page_id']
        history_id = request.form['history_id']
        forward = request.form['forward']
        back = prev_page_id

        # Checks that the user is logged in
        if not guest:
            # Records the page visit to story activity
            user_activity = UserActivity.get_user_activity(current_user.email)
            user_activity.story_activity.append({
                'timestamp': datetime.now(),
                'story': story,
                'page_id': page_id
                })
            user_activity.save()

            # Checks if a history ID is included, if not a new history is added
            if history_id == '':
                # Checks for a matching history, to not add a duplicate history
                history_found = False
                for index, history in enumerate(current_user.history):
                    if history['story'] == story and history['pages'][0] == page_id and len(history['pages']) == 1:
                        history['last_updated'] = datetime.now()
                        history_found = True
                        history_id = index

                # If a matching history does not already exists, adds the root page to a new history
                if not history_found:
                    new_history = {}
                    new_history['last_updated'] = datetime.now()
                    new_history['story'] = story
                    new_history['pages'] = [page_id]
                    current_user.history.append(new_history)
                    history_id = len(current_user.history) - 1

                # Saves the changes to the user
                current_user.save()

            # If a history ID is include, we will edit it according to the user's behavior
            else:
                history_id = int(history_id)
                history = current_user.history[history_id]

                # If the user is moving forwards
                if forward:
                    # Checks if the current page is already included in the history, if not it is added
                    if page_id not in current_user.history[history_id]['pages']:
                        # Checks if the previous page the user visited is the last page recorded in the current history
                        if prev_page_id == current_user.history[history_id]['pages'][-1]:
                            # Appends the current page to the current history and updates the timestamp
                            current_user.history[history_id]['pages'].append(page_id)
                            current_user.history[history_id]['last_updated'] = datetime.now()

                        # If the previous page is not the last page recorded, then the user is branching off from their previous
                        # path, making a new decision. In this case, we want to copy the path up to the point of the previous page
                        # and add the current page
                        else:
                            new_history = {}
                            new_history['pages'] = []
                            for p in current_user.history[history_id]['pages']:
                                new_history['pages'].append(p)
                                if p == prev_page_id:
                                    break
                            new_history['pages'].append(page_id)
                            new_history['story'] = current_user.history[history_id]['story']
                            new_history['last_updated'] = datetime.now()
                            current_user.history.append(new_history)
                            history_id = len(current_user.history) - 1

                        # Checks that the history updated or the new history created does not match another history. If there is one,
                        # removes the current history and switches to the matching one that already existed
                        for index, h1 in enumerate(current_user.history):
                            for h2 in current_user.history:
                                if h1 != h2 and len(h1['pages']) == len(h2['pages']):
                                    history_matches = True
                                    for p in range(len(h1['pages'])):
                                        if h1['pages'][p] != h2['pages'][p]:
                                            history_matches = False
                                    if history_matches:
                                        current_user.history.remove(h2)
                                        h1['last_updated'] = datetime.now()
                                        history_id = index

                    # If the history already contains the current page, we can just update the timestamp, but
                    # we don't want to add a duplicate page ID
                    else:
                        current_user.history[history_id]['last_updated'] = datetime.now()

                    # Saves the changes to the user
                    current_user.save()

                # If the user is moving backwards
                else:
                    # Add any behavior for backwards navigation here
                    pass

                # Sets the back page to the previous page in the history
                back = None
                back_name = None
                for p in current_user.history[history_id]['pages']:
                    if p == page_id:
                        break
                    back = p
                    # Gets the page name of the page that the back button points to
                    back_name = story_doc.get('page_nodes.`' + back + '`')['page_name']

        # Returns the story_page.html template with the specified page
        return render_response(render_template("story_page.html", guest=guest, favorited=favorited, story=story, page=page, preview=preview, back=back, back_name=back_name, history=history_id))

    # Checks that the user is logged in and not previewing the page
    # Adds a new history in case the user gets to this page from an external link that wouldn't include the information to append the page to a history
    if not preview and not guest:
        # Checks for a matching history, to not add a duplicate history
        history_found = False
        for history in current_user.history:
            if history['story'] == story and history['pages'][0] == page_id and len(history['pages']) == 1:
                history['last_updated'] = datetime.now()
                history_found = True

        # If a matching history does not already exists, adds the root page to a new history
        if not history_found:
            new_history = {}
            new_history['last_updated'] = datetime.now()
            new_history['story'] = story
            new_history['pages'] = [page_id]
            current_user.history.append(new_history)

        # Saves the changes to the user
        current_user.save()

    # Returns the story_page.html template with the specified page
    return render_response(render_template("story_page.html", guest=guest, favorited=favorited, story=story, page=page, preview=preview))


# Serves the editor page
@app.route('/editor')
@admin_login_required
def myedit():
    # Returns the editor.html template with the given values
    return render_template('editor.html')


# Serves the editor page
@app.route('/openeditor')
@admin_login_required
def openedit():
    # Returns the editor.html template with the given values
    return render_template('openeditor.html')


@app.route('/forward/', methods=["POST"])
@admin_login_required
def move_forward():
    render_template('openeditor.html', button_color="blue")


# Serves the upload page
@app.route('/upload', methods=['GET', 'POST'])
@admin_login_required
def upload():
    # Checks to see if the HTML method request is 'POST'
    if request.method == 'POST':
        # Checks to make sure a file was uploaded
        if 'file' not in request.files:
            return render_response(redirect(request.url))
        file = request.files['file']
        # Checks to make sure the file has an actual name and not just empty
        if file.filename == '':
            return render_response(redirect(request.url))
        # Checks to make sure the file extension/type is allowed
        if '.' in file.filename and file.filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']:
            # Secures the file name for security purposes
            filename = secure_filename(file.filename)
            # Saves the file in the specified upload folder
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        return render_response(redirect(url + url_for('media')))
    # Returns the file_upload.html template with the given values
    return render_template('admin_pages/file_upload.html')


# Serves the media manager page
@app.route('/media')
@admin_login_required
def media():
    """media()
    Serves the media manager page
    Accessed at '/media' via a GET request
    Requires that the user is logged in as an admin
    """

    # The file names
    files = []

    # Gets the names of all files in the file_uploads folder
    for file in os.listdir('file_uploads'):
        seconds = os.path.getmtime('file_uploads/' + file)
        timestamp = time.ctime(seconds)
        sizeb = os.stat('file_uploads/' + file).st_size
        sizek = sizeb/1024
        sizeg = round(sizek/1024, 2)
        sizek = round(sizek, 2)
        size = sizek
        sizetype = 'KB'
        if sizeg > 2:
            size = sizeg
            sizetype = 'GB'
        files.append([file, timestamp, size, sizetype])

    # Returns the files page with the files
    return render_response(render_template('admin_pages/media_manager.html', files=files, url_root=request.url_root))


# Serves the page of an open file
@app.route('/file/<file>')
def open_file(file):
    filePath = app.config['UPLOAD_FOLDER'] + '/'
    return send_from_directory(filePath, file)


# @app.route('/rename_file', methods=['POST'])
# @admin_login_required
# def rename_file():
#     file = request.form['file']
#     fileRename = request.form['rename']
#     fileType = file.rsplit('.', 1)[1].lower()
#     fileRename = fileRename + '.' + fileType
#     src = os.path.join(app.config['UPLOAD_FOLDER'], file)
#     dst = os.path.join(app.config['UPLOAD_FOLDER'], fileRename)
#     os.rename(src, dst)
#     return json.dumps({'success': True}), 200, {'ContentType': 'application/json'}


@app.route('/download_file/<file>')
@admin_login_required
def download_file(file):
    path = os.path.join(app.config['UPLOAD_FOLDER'], file)
    return send_file(path, as_attachment=True, attachment_filename=file)


@app.route('/delete_file/<file>')
@admin_login_required
def delete_file(file):
    path = os.path.join(app.config['UPLOAD_FOLDER'], file)
    os.remove(path)
    return render_response(redirect(url + url_for('media')))


@app.route('/admin/editor')
@admin_login_required
def editor():
    input_file_name = 'story_editing/demo_html/GA_draft.html'
    import_id = '2000'
    firestoreTwineConvert(db, input_file_name, import_id)
    return 'Success!'


@app.route('/admin/twine')
@admin_login_required
def twine():
    twine_files = [
        {
            'path': 'story_editing/demo_html/demo-story.html',
            'id': 'demo_01'
        },
        {
            'path': 'story_editing/demo_html/GA_draft.html',
            'id': 'GA_draft_01'
        },
        {
            'path': 'story_editing/demo_html/subtree.html',
            'id': 'demo_02'
        }
    ]
    file_index = 0
    for file in twine_files:
        file_index += 1
        firestoreTwineConvert(db, file['path'], file['id'])
    return f'imported {file_index} stories'


if __name__ == '__main__':
    # Run the application on the specified IP address and port
    if platform == 'local':
        app.run(host='localhost', port=8080, debug=True)
    else:
        app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)), debug=False)
