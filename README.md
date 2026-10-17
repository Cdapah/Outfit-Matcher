# 👗 Outfit Matcher

**Votre dressing, vos envies, votre tenue du jour.**

Outfit Matcher est une application web qui aide à composer des tenues à partir des vêtements que l’on possède déjà, selon la météo, l’occasion et ses préférences.

🔗 **[Essayer l’application](https://outfit.89.168.63.222.sslip.io)**

## ✨ Fonctionnalités

- **Garde-robe personnelle** : ajouter ses vêtements avec une photo, un type, une couleur et un motif.
- **Import de photos** : sélectionner des images dans la galerie ou les fichiers, avec un accès caméra sur Android.
- **Détourage des vêtements** : retirer le fond des photos et retoucher le résultat.
- **Tenues par occasion** : quotidien, cours, travail, sortie, fête, sport et rendez-vous.
- **Météo** : adapter les propositions aux conditions du jour.
- **Personnalisation** : renseigner son style, ses préférences de coupe et sa sensibilité au froid.
- **Composition des looks** : garder une pièce et remplacer un autre vêtement compatible.
- **Favoris et avis** : enregistrer ses tenues préférées et évaluer les propositions.
- **Suggestions de pièces complémentaires** : identifier des vêtements pouvant compléter plusieurs associations.
- **Gestion du compte** : retrouver sa garde-robe après connexion, exporter ses données et supprimer son compte.

## 📱 Utilisation

1. Créer un compte ou se connecter.
2. Ajouter quelques vêtements à sa garde-robe.
3. Choisir son programme du jour.
4. Découvrir les tenues proposées.
5. Enregistrer un look ou remplacer une pièce.

L’application est accessible depuis un navigateur sur ordinateur, iPhone et Android.

## 🛠️ Technologies

| Partie | Technologies |
| --- | --- |
| Interface | HTML, CSS, JavaScript |
| API | Python, FastAPI |
| Serveur applicatif | Uvicorn |
| Traitement des images | Pillow |
| Déploiement | Docker, Docker Compose |
| HTTPS | Caddy |
| Hébergement | Oracle Cloud, Ubuntu |

## 🚀 Installation locale

### Prérequis

- Git
- Docker et Docker Compose

### Démarrage

Cloner ce dépôt, puis ouvrir le dossier du projet :

    git clone URL_DE_CE_DEPOT
    cd NOM_DU_DEPOT

Préparer la configuration à partir du modèle :

    cp .env.example .env

Adapter les valeurs du fichier `.env`, puis démarrer l’application :

    docker compose up -d --build app

Vérifier son fonctionnement :

    curl http://127.0.0.1:8002/health

L’application est disponible à l’adresse :

    http://127.0.0.1:8002

Pour un déploiement public, configurer le domaine, HTTPS et les paramètres de production.

## 📂 Organisation du projet

    .
    ├── server.py          # API, comptes et gestion des données
    ├── static/            # Interface et ressources de l’application
    ├── tests/             # Tests automatisés
    ├── ops/               # Scripts d’exploitation et de déploiement
    ├── Dockerfile
    ├── compose.yaml
    ├── Caddyfile
    ├── requirements.txt
    └── .env.example

## 🔒 Données personnelles

Les comptes et les photos sont conservés sur le serveur de l’application. Chaque utilisateur retrouve sa garde-robe dans son compte.

La localisation est facultative et sert à obtenir la météo. Une ville peut également être renseignée manuellement.

Les fonctions d’export et de suppression permettent de gérer ses données depuis l’application.

**Pour les contributeurs :** ne jamais publier de fichier `.env`, de clé SSH, de base de données, de sauvegarde ou de photo utilisateur dans ce dépôt.

## 🚧 État du projet

Outfit Matcher est un projet en évolution, testé progressivement grâce aux retours des utilisateurs.

Les propositions reposent sur les caractéristiques renseignées pour les vêtements. Leur pertinence dépend donc de la qualité des informations ajoutées à la garde-robe.

L’essayage virtuel réaliste et l’avatar Premium restent en préparation. Les visuels d’inspiration ne constituent pas une simulation fidèle des vêtements portés.

## 👩🏾‍💻 Autrice

**Carole Dapah**

- GitHub : [Cdapah](https://github.com/Cdapah)
- Portfolio : [cdapah.github.io/Portfolio](https://cdapah.github.io/Portfolio/)

## 💬 Retours

Une idée, un problème ou une suggestion ? Ouvrez une **Issue** dans ce dépôt en précisant les étapes suivies, le navigateur et le téléphone utilisés.
