import os, sys, re, json, string, random
from annotate_bed import MainApp
from flask import Flask, render_template, request, redirect, flash, send_from_directory, session, jsonify, copy_current_request_context
from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileRequired
from werkzeug.utils import secure_filename
from wtforms import StringField, SubmitField, FileField, RadioField, SelectMultipleField
from wtforms.widgets import ListWidget, CheckboxInput
from wtforms.validators import DataRequired, Length, Required, regexp, data_required
from flask import url_for
from flask_bootstrap import Bootstrap
from multiprocessing.pool import ThreadPool
import re
import glob

from pymongo import MongoClient

app = Flask(__name__)
bootstrap = Bootstrap(app)
pool = ThreadPool(processes=1)
finished = False

UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif'}
ANNOT_CHOICES = [('all_beds','All'),  ('gene', 'Genes'), ('coding_gene','Coding genes'),
                 ('noncoding_gene','Non-coding genes'), ('longNC','Long non-coding'), ('mirna','miRNA'),
                 ('pseudogene','Pseudogenes'), ('circRNA','circRNA'), ('enhancer','Enhancers'),
                 ('ucr', 'UCR'), ('har', 'HAR')]

EXPLANATIONS = {"gene": "All RefSeq genes reported in UCSC genome browser.",
                "coding_gene": "RefSeq protein-coding genes (labelled as NM_*) reported in UCSC genome browser.",
                "noncoding_gene": "RefSeq non-protein-coding genes (labelled as NR_*) reported in UCSC genome browser.",
                "longNC": "Long non-coding RNAs reported in LNCipedia.",
                "mirna": "microRNAs, reported in miRBase, and microRNA targets, reported in DIANA-TarBase and "
                        "in TargetScan.",
                "circRNA": "Circular RNAs reported in circBase.", "pseudogene": "Pseudogenes reported in psiDR.",
                "ucr": "Ultraconserved elements (UCRs) reported in UCbase.",
                "har": "Human Accelerated Regions (HARs).",
                "enhancer": "Enhancers reported in Human Enhancer Disease Database (HEDD)."}

# annot_dict = {}
# for a in ANNOT_CHOICES:
#     annot_dict[a[0]] = a[1]


GENELISTS = [('all_genelists','All'), ('ID', 'Intellectual Disability'), ('dosage_sensitive', 'Dosage sensitive'),
             ('mendeliome', 'Mendeliome'),
             ('ohnologs', 'Ohnologs'), ('imprinted', 'Imprinted')]

app.config['SECRET_KEY'] = 'AGATTAcanvas2018'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_PATH'] = 5000



#
#     return '''
#     <!doctype html>
#     <title>Upload new File</title>
#     <h1>Upload new File</h1>
#     <form method=post enctype=multipart/form-data>
#       <p><input type=file name=file>
#          <input type=submit value=Upload>
#     </form>
#     '''
#
# @app.route('/uploads/<filename>')
# def uploaded_file(filename):
#     return send_from_directory(app.config['UPLOAD_FOLDER'],
#
#          filename)

def atoi(text):
    return int(text) if text.isdigit() else text

def natural_keys(text):
    '''
    alist.sort(key=natural_keys) sorts in human order
    '''
    return [ atoi(c) for c in re.split('(\d+)', text) ]

def id_generator(size=8, chars=string.ascii_lowercase + string.digits):
    return ''.join(random.choice(chars) for _ in range(size))


class Object(object):
    pass


def setattrs(_self, **kwargs):
    for k,v in kwargs.items():
        setattr(_self, k, v)
        
class MultiCheckboxField(SelectMultipleField):
    widget = ListWidget(prefix_label=False)
    option_widget = CheckboxInput()
    
class MainForm(FlaskForm):
    line_input = StringField(u'Genomic region ', validators=[Length(max=50)])
    upload = FileField('Input file', validators=[
        FileAllowed(['txt', 'csv', 'cnv'], 'Text only!')
    ])
    window = StringField(u'Window (bp):', validators=[Length(max=15)], default='1000000')
    annot = MultiCheckboxField('annot', choices=ANNOT_CHOICES)
    genes = MultiCheckboxField('genes', choices=GENELISTS)
    submit = SubmitField("Submit")



# class FileForm(FlaskForm):
#     filein = FileField("Input file", validators=[DataRequired()])
#     submit = SubmitField("Submit")

@app.route('/', methods=['GET', 'POST'])
def index():
    session['ann_choices'] = []
    session['genes_choices'] = []
    form = MainForm()

    if form.validate_on_submit():
        session['task_id'] = id_generator()
        os.mkdir(os.path.join(app.config['UPLOAD_FOLDER'], session['task_id']))
        if 'radio2' in request.form:
            session['choice'] = 'file'
            session['cnv_line'] = None
            f = form.upload.data
            session['filename'] = secure_filename(f.filename)
            session['working_filename'] = os.path.join(session['task_id'], session['task_id']+'.csv')
            f.save(os.path.join(
                app.config['UPLOAD_FOLDER'], session['working_filename']
            ))
        elif 'radio1' in request.form:
            session['choice'] = 'line'
            session['working_filename'] = os.path.join(session['task_id'], session['task_id']+'.csv')
            session['cnv_line'] = request.form['line_input']
        
        if 'radio-hg19' in request.form:
            session['hg'] = 'hg19'
        elif 'radio-hg18' in request.form:
            session['hg'] = 'hg18'
        elif 'radio-hg38' in request.form:
            session['hg'] = 'hg38'
        print("REF:", session['hg'])

        for elem in request.form.getlist('annot'):
                session['ann_choices'].append(elem)
        if 'mirna' in request.form.getlist('annot'):
            session['ann_choices'].append('mirbase')
        
        print(request.form.getlist('genes'))
        for elem in request.form.getlist('genes'):
                if elem != "all_genelists":
                    session['genes_choices'].append(elem+'_genelist')
            
        if 'distance' in request.form:
            session['distance'] = request.form['distance']
        else:
            session['distance'] = 1000000
            
        print("ANNOT:", session['ann_choices'])
        print("GENES:", session['genes_choices'])
        print("DISTANCE", session['distance'])
        return redirect(url_for('working'))

    return render_template('index.html', form=form)


@app.route('/working.html', methods=['GET', 'POST'])
def working():
    global async_result
    global finished
    finished = False

    session['file_out'] = os.path.join(app.config['UPLOAD_FOLDER'], "{}.xlsx".format(
        os.path.splitext(session['working_filename'])[0]))
    
    args = Object
    setattrs(args, cnv_file=None, cnv_line=None, all_beds=False, circRNA=False,
             coding_gene=False, gene=False, longNC=False, mirna=False, mirbase=False,
             noncoding_gene=False, pseudogene=False, ucr=False, har=False, enhancer=False,
             all_genelists=False,
             ID_genelist=False, dosage_sensitive_genelist=False, imprinted_genelist=False,
             mendeliome_genelist=False,
             ohnologs_genelist=False, distance=session['distance'],
             out=session['file_out'])


    if session['choice'] == 'file':
        session['download_name'] = os.path.splitext(session['filename'])[0] + '_INCAS.xlsx'
        setattrs(args, cnv_line=None)
        setattrs(args, cnv_file=os.path.join(app.config['UPLOAD_FOLDER'], session['working_filename']))
    elif session['choice'] == 'line':
        session['download_name'] = 'INCAS_results.xlsx'
        setattrs(args, cnv_line=session['cnv_line'])
        setattrs(args, cnv_file=None)
    
    for elem in session['ann_choices']:
        setattr(args, elem, True)
    
    for elem in session['genes_choices']:
        setattr(args, elem, True)
        
  
    async_result = pool.apply_async(worker, (args,))
    
    return render_template('working.html', file=session['filename'])
    # return render_template('results.html', file_out=file_out, download_name=download_name, result_db=result_db)


def worker(args):
    global finished
    success = MainApp(args).process()
    if success != 0:
        finished = -1
    else:
        finished = True
    
@app.route('/status')
def thread_status():
    global async_result
    
    """ Return the status of the worker thread """
    # f_read = open(session['file_out'].replace('.xlsx','_log.txt'), "r").readlines()
    # # st_results = os.stat(session['file_out'].replace('.xlsx','_log.txt'))
    # # st_size = st_results[6]
    # # f_read.seek(st_size)
    progress = [os.path.basename(x).split('.')[0] for x in glob.glob(os.path.dirname(session['file_out'])+'/*.progress')]
    progress.sort(key=natural_keys)
    
    if finished == True:
        return jsonify(dict(status='finished'))
    elif finished == -1:
        return jsonify(dict(status='problem'))
    else:
        return jsonify(dict(status=progress))

@app.route('/results.html', methods=['GET', 'POST'])
def results():
    if 'all_beds' in session['ann_choices']:
        print(session)
        print(session['ann_choices'])
        session['ann_choices'].remove('all_beds')
        
    if 'all_genelists' in session['ann_choices']:
        session['ann_choices'].remove('all_genelists')
        
    return render_template('results.html', json_out=re.sub('.xlsx', '.json', session['file_out']),
                           file_out=session['file_out'],
                           text_file_out=re.sub('.xlsx', '.csv', session['file_out']),
                           download_name=session['download_name'],
                           text_download_name=re.sub('.xlsx', '.csv', session['download_name']),
                           choices=session['ann_choices'], genes_choices=session['genes_choices'],
                           info=EXPLANATIONS)

@app.route('/error.html', methods=['GET', 'POST'])
def problem():
    return render_template('error.html', file=session['filename'])

if __name__ == '__main__':
    app.run()
