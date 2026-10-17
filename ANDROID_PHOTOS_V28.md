# Outfit Matcher — Photos Android et Samsung (V28)

Le champ photo a été remis dans la disposition de la version Sport, sans les textes d’aide supplémentaires. Une seule barre native permet de choisir une photo dans la galerie ou les fichiers. Sur Android, le petit lien « 📷 Prendre une photo » ouvre directement l'appareil photo. La galerie et les fichiers restent accessibles par le sélecteur natif, dont la présentation dépend d'Android et du navigateur. JPG/JPEG, PNG et WebP sont pris en charge.

Les octets du fichier choisi sont copiés avant que le formulaire vide le sélecteur ; cela préserve les JPEG issus de la galerie Android. Si le navigateur n'arrive pas à décoder un JPEG, le fichier original est envoyé une seule fois au serveur de l'application, qui vérifie le compte et le format réel puis enregistre la photo sous forme PNG. Limite de 25 Mo et de 25 mégapixels ; les autres endpoints restent limités à 5 Mo. Aucun prestataire externe de conversion n'est appelé.

Installation sur le serveur après transfert de cette archive dans ~/ :

```bash
cd ~/outfit-matcher-oracle &&
unzip -t ~/Outfit_Matcher_Android_Photos.zip &&
unzip -o ~/Outfit_Matcher_Android_Photos.zip &&
sudo bash ops/deploy.sh
```

Contrôlez le déploiement avec `curl -sS http://127.0.0.1:8002/health` : la réponse doit indiquer `"version":"28"`. Fermez et rouvrez la page ou rafraîchissez-la sur Samsung et essayez un JPEG provenant de la galerie ainsi qu'une photo prise avec l'appareil. Vérifiez que la pièce demeure dans le compte après reconnexion. Tests automatisés réussis ; le comportement exact d'un téléphone Samsung physique doit être confirmé après déploiement.

L'archive ne contient aucune donnée utilisateur ni configuration du serveur.
