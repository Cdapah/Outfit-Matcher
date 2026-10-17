import io
import os
import tempfile
import unittest
import uuid

TEMP = tempfile.TemporaryDirectory()
os.environ['OUTFIT_DATA'] = TEMP.name
os.environ['COOKIE_SECURE'] = '0'
os.environ['PUBLIC_ORIGIN'] = 'http://testserver'

from fastapi.testclient import TestClient
from PIL import Image
from server import app, COOKIE


class AccountFlow(unittest.TestCase):
    def setUp(self):
        self.context = TestClient(app, headers={'Origin': 'http://testserver'})
        self.client = self.context.__enter__()
        self.email = f'{uuid.uuid4()}@example.com'
        self.credentials = {'email': self.email, 'password': 'demo-password-123', 'name': 'Test'}
        response = self.client.post('/api/auth/register', json=self.credentials)
        self.assertEqual(response.status_code, 200)

    def tearDown(self):
        self.context.__exit__(None, None, None)

    def upload(self):
        image = io.BytesIO()
        Image.new('RGB', (30, 30), 'blue').save(image, 'JPEG')
        photo_id = str(uuid.uuid4())
        response = self.client.put('/api/photo/' + photo_id, content=image.getvalue(), headers={'Content-Type': 'image/jpeg'})
        self.assertEqual(response.status_code, 200)
        return '/api/photo/' + photo_id

    def test_profile_is_private_and_persists(self):
        photo=self.upload()
        body={'settings':{'profilePhoto':photo,'heightCm':172,'clothingSize':'M','hair':'Carré','onboardingDone':True},'items':[]}
        self.assertEqual(self.client.put('/api/state',json=body).status_code,200)
        self.client.post('/api/auth/logout')
        self.client.post('/api/auth/login',json=self.credentials)
        settings=self.client.get('/api/state').json()['settings']
        self.assertEqual(settings['profilePhoto'],photo)
        self.assertEqual(settings['heightCm'],172)
        self.assertTrue(settings['onboardingDone'])
        with TestClient(app,headers={'Origin':'http://testserver'}) as other:
            other.post('/api/auth/register',json={'email':f'{uuid.uuid4()}@example.com','password':'demo-password-123','name':'Other'})
            self.assertEqual(other.put('/api/state',json=body).status_code,400)
        body['settings']['heightCm']=999
        self.client.put('/api/state',json=body)
        self.assertIsNone(self.client.get('/api/state').json()['settings']['heightCm'])

    def test_experience_settings_and_availability_survive_login(self):
        prefs = {'fit':'ample','shoes':'flat','length':'long','cold':'sensitive'}
        body = {'settings': {'preferences':prefs, 'formality':'smart', 'favorites':['date|1|2|3'], 'worn':[{'key':'date|1|2|3','ids':[1,2,3],'at':12345}]}, 'items':[{'id':1,'name':'Pull','category':'haut','availability':'laundry'}]}
        self.assertEqual(self.client.put('/api/state',json=body).status_code,200)
        self.client.post('/api/auth/logout')
        self.client.post('/api/auth/login',json=self.credentials)
        restored=self.client.get('/api/state').json()
        self.assertEqual(restored['settings']['preferences'],prefs)
        self.assertEqual(restored['settings']['favorites'],['date|1|2|3'])
        self.assertEqual(restored['settings']['worn'][0]['ids'],[1,2,3])
        self.assertEqual(restored['items'][0]['availability'],'laundry')
        self.assertEqual(restored['settings']['lookView'],'pieces')

    def test_all_seven_occasions_including_sport_persist(self):
        occasions = ['quotidien', 'cours', 'travail', 'sortie', 'fete', 'date', 'sport']
        body = {'settings': {}, 'items': [{'id': 1, 'name': 'Pull', 'category': 'haut', 'occasions': occasions}]}
        self.assertEqual(self.client.put('/api/state', json=body).status_code, 200)
        self.client.post('/api/auth/logout')
        self.client.post('/api/auth/login', json=self.credentials)
        self.assertEqual(self.client.get('/api/state').json()['items'][0]['occasions'], occasions)

    def test_sport_catalogue_and_occasion_survive_login(self):
        kinds = [('sports_bra','haut'),('sports_tshirt','haut'),('sports_tank','haut'),('sports_shorts','bas'),('running_shoes','chaussures'),('sports_jacket','veste'),('windbreaker','veste'),('sports_bag','accessoire')]
        pieces = [{'id':i+1,'kind':kind,'category':cat,'occasions':['sport']} for i,(kind,cat) in enumerate(kinds)]
        self.assertEqual(self.client.put('/api/state',json={'settings':{},'items':pieces}).status_code,200)
        self.client.post('/api/auth/logout')
        self.client.post('/api/auth/login',json=self.credentials)
        restored = self.client.get('/api/state').json()['items']
        self.assertEqual([p['kind'] for p in restored],[p['kind'] for p in pieces])
        self.assertTrue(all(p['occasions']==['sport'] for p in restored))

    def test_logout_relogin_restores_photo_and_wardrobe(self):
        photo = self.upload()
        body = {'settings': {'name': 'Test', 'hair': 'carré bouclé'}, 'items': [{'id': 1, 'name': 'Jean bleu', 'category': 'bas', 'color': 'bleu', 'photo': photo}]}
        self.assertEqual(self.client.put('/api/state', json=body).status_code, 200)
        before = self.client.get(photo).content
        self.assertEqual(self.client.post('/api/auth/logout').status_code, 200)
        self.assertEqual(self.client.get('/api/state').status_code, 401)
        login = self.client.post('/api/auth/login', json={**self.credentials, 'email': self.email.upper()})
        self.assertEqual(login.status_code, 200)
        restored = self.client.get('/api/state').json()
        self.assertEqual(restored['items'][0]['photo'], photo)
        self.assertEqual(restored['settings']['hair'], 'carré bouclé')
        self.assertEqual(self.client.get(photo).content, before)
        # Restart the app context: sessions, metadata and photos persist on disk.
        cookie = self.client.cookies.get(COOKIE)
        with TestClient(app) as restarted:
            restarted.cookies.set(COOKIE, cookie)
            self.assertEqual(restarted.get('/api/state').json()['items'][0]['name'], 'Jean bleu')
            self.assertEqual(restarted.get(photo).content, before)

    def test_other_account_cannot_read_or_reference_photo(self):
        photo = self.upload()
        with TestClient(app, headers={'Origin': 'http://testserver'}) as other:
            other.post('/api/auth/register', json={'email': f'{uuid.uuid4()}@example.com', 'password': 'other-password-123'})
            self.assertEqual(other.get(photo).status_code, 404)
            self.assertEqual(other.put('/api/state', json={'settings': {}, 'items': [{'id': 1, 'name': 'Photo', 'category': 'haut', 'photo': photo}]}).status_code, 400)

    def test_cross_origin_writes_are_blocked(self):
        response = self.client.put('/api/state', headers={'Origin': 'https://unrelated.example'}, json={'settings': {}, 'items': []})
        self.assertEqual(response.status_code, 403)

    def test_invalid_upload_and_state_do_not_crash(self):
        response = self.client.put('/api/photo/' + str(uuid.uuid4()), content=b'not-an-image')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.put('/api/state', json=[]).status_code, 400)
        self.assertEqual(self.client.put('/api/state', json={'settings': {}, 'items': [{'id': 1, 'category': []}]}).status_code, 400)

    def test_samsung_jpeg_raw_upload_private_and_normalized(self):
        image = io.BytesIO()
        Image.new('RGB', (300, 200), '#bd8531').save(image, 'JPEG', quality=90)
        payload = image.getvalue()
        route = '/api/photo/' + str(uuid.uuid4())
        with TestClient(app, headers={'Origin': 'http://testserver'}) as other:
            self.assertEqual(other.put(route, content=payload).status_code, 401)
        self.assertEqual(self.client.put(route, content=b'not-a-jpeg').status_code, 400)
        self.assertEqual(self.client.put(route, content=payload, headers={'Origin': 'https://other.example'}).status_code, 403)
        response = self.client.put(route, content=payload, headers={'Content-Type': 'application/octet-stream'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Image.open(io.BytesIO(self.client.get(route).content)).format, 'PNG')

    def test_large_jpeg_can_be_uploaded_but_other_endpoints_stay_limited(self):
        image = io.BytesIO()
        Image.frombytes('RGB', (2200, 2200), os.urandom(2200 * 2200 * 3)).save(image, 'JPEG', quality=96)
        payload = image.getvalue()
        self.assertGreater(len(payload), 5_000_000)
        route = '/api/photo/' + str(uuid.uuid4())
        self.assertEqual(self.client.put('/api/state', content=payload).status_code, 413)
        self.assertEqual(self.client.put(route, content=payload).status_code, 200)
        self.assertEqual(Image.open(io.BytesIO(self.client.get(route).content)).format, 'PNG')

    def test_logout_revokes_old_session(self):
        token = self.client.cookies.get(COOKIE)
        self.client.post('/api/auth/logout')
        self.client.cookies.set(COOKIE, token)
        self.assertEqual(self.client.get('/api/state').status_code, 401)

    def test_cabin_and_original_photo_persist_for_every_account(self):
        original, cutout = self.upload(), self.upload()
        fitting = {'v': 1, 'photo': cutout, 'transforms': {
            'balanced': {'dx': 12, 'dy': -8, 'scale': 1.18, 'rotation': 4, 'pair': False},
            'curvy': {'dx': 0, 'dy': 6, 'scale': 1.1, 'rotation': 0, 'pair': False}}}
        avatar = {'shape': 'curvy', 'skin': 'cocoa', 'hair': 'braids', 'hairColor': 'black'}
        body = {'settings': {'avatar': avatar, 'hair': 'Tresses', 'lookView': 'avatar'}, 'items': [
            {'id': 42, 'name': 'Pull', 'category': 'haut', 'photo': cutout, 'sourcePhoto': original,
             'cutout': True, 'cutoutBounds': {'x': .1, 'y': .1, 'w': .8, 'h': .8}, 'fitting': fitting}]}
        self.assertEqual(self.client.put('/api/state', json=body).status_code, 200)
        self.client.post('/api/auth/logout')
        self.client.post('/api/auth/login', json=self.credentials)
        restored = self.client.get('/api/state').json()
        self.assertEqual(restored['settings']['avatar'], avatar)
        self.assertEqual(restored['items'][0]['fitting'], fitting)
        self.assertEqual(restored['items'][0]['sourcePhoto'], original)
        self.assertEqual(self.client.get(original).status_code, 200)
        self.assertEqual(self.client.delete(original).status_code, 409)
        with TestClient(app, headers={'Origin': 'http://testserver'}) as other:
            other.post('/api/auth/register', json={'email': f'{uuid.uuid4()}@example.com', 'password': 'other-password-123'})
            other_photo = str(uuid.uuid4())
            image = io.BytesIO(); Image.new('RGBA', (40, 50), 'green').save(image, 'PNG')
            other.put('/api/photo/' + other_photo, content=image.getvalue())
            own_url = '/api/photo/' + other_photo
            own = {'settings': {'avatar': {'shape': 'broad'}}, 'items': [
                {'id': 42, 'category': 'haut', 'name': 'Chemise', 'photo': own_url,
                 'fitting': {'v': 1, 'photo': own_url, 'transforms': {'broad': {'dx': -5}}}}]}
            self.assertEqual(other.put('/api/state', json=own).status_code, 200)
            self.assertEqual(other.get('/api/state').json()['items'][0]['fitting']['transforms']['broad']['dx'], -5)
            self.assertEqual(other.get(original).status_code, 404)
            own['items'][0]['sourcePhoto'] = original
            self.assertEqual(other.put('/api/state', json=own).status_code, 400)
        self.assertEqual(self.client.get('/api/state').json()['items'][0]['fitting'], fitting)

    def test_replacement_photo_does_not_inherit_old_placement(self):
        old, new = self.upload(), self.upload()
        body = {'settings': {}, 'items': [{'id': 2, 'category': 'bas', 'name': 'Jean', 'photo': new,
            'fitting': {'v': 1, 'photo': old, 'transforms': {'balanced': {'dx': 70, 'scale': 2}}}}]}
        self.assertEqual(self.client.put('/api/state', json=body).status_code, 200)
        self.assertEqual(self.client.get('/api/state').json()['items'][0]['fitting'], {})

    def test_cabin_fields_are_bounded_and_whitelisted(self):
        photo = self.upload()
        body = {'settings': {'avatar': {'shape': ['bad'], 'skin': 'cocoa', 'hair': '<script>', 'extra': 'ignored'}}, 'items': [
            {'id': 3, 'category': 'robe', 'name': 'Robe', 'photo': photo,
             'fitting': {'v': 1, 'photo': photo, 'transforms': {'balanced': {'dx': 99999, 'dy': -99999, 'scale': True, 'rotation': 360, 'pair': 'yes'}, 'unknown': {'dx': 5}}}}]}
        self.assertEqual(self.client.put('/api/state', json=body).status_code, 200)
        restored = self.client.get('/api/state').json()
        self.assertEqual(restored['settings']['avatar'], {'skin': 'cocoa'})
        self.assertEqual(restored['items'][0]['fitting']['transforms'], {'balanced': {'dx': 180, 'dy': -220, 'scale': 1, 'rotation': 45, 'pair': False}})


if __name__ == '__main__':
    unittest.main()
