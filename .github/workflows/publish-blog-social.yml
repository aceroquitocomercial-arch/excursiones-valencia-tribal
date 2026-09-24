name: Publicar posts del blog en redes sociales

# Detecta automáticamente los posts nuevos publicados en valenciatribal.com
# (WordPress) y los publica en la Página de Facebook y en Instagram sin
# intervención manual.
#
# Es el complemento automático de "Publicar en redes sociales"
# (.github/workflows/publish-social.yml): aquel workflow sigue disponible
# para publicar algo puntual a mano (workflow_dispatch); este corre solo,
# cada 15 minutos, detectando por sí mismo qué posts son nuevos.
#
# No requiere ningún secret nuevo: reutiliza los mismos que ya usan
# publish-social.yml y la automatización de Reels (FACEBOOK_*, INSTAGRAM_*,
# R2_*).

on:
  schedule:
    - cron: '*/15 * * * *'
  workflow_dispatch: {}

permissions:
  contents: write

concurrency:
  group: publish-blog-social
  cancel-in-progress: false

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Instalar dependencias
        run: |
          sudo apt-get update -y && sudo apt-get install -y fonts-dejavu-core
          pip install -r requirements.txt

      - name: Detectar posts nuevos y publicar en Facebook/Instagram
        env:
          FACEBOOK_PAGE_ACCESS_TOKEN: ${{ secrets.FACEBOOK_PAGE_ACCESS_TOKEN }}
          FACEBOOK_PAGE_ID: ${{ secrets.FACEBOOK_PAGE_ID }}
          INSTAGRAM_ACCESS_TOKEN: ${{ secrets.INSTAGRAM_ACCESS_TOKEN }}
          INSTAGRAM_USER_ID: ${{ secrets.INSTAGRAM_USER_ID }}
          R2_ACCOUNT_ID: ${{ secrets.R2_ACCOUNT_ID }}
          R2_ACCESS_KEY_ID: ${{ secrets.R2_ACCESS_KEY_ID }}
          R2_SECRET_ACCESS_KEY: ${{ secrets.R2_SECRET_ACCESS_KEY }}
          R2_BUCKET_NAME: ${{ secrets.R2_BUCKET_NAME }}
          R2_PUBLIC_DOMAIN: ${{ secrets.R2_PUBLIC_DOMAIN }}
        run: python3 scripts/publish_blog_to_social.py

      - name: Commit y push del estado (docs/blog_social_state.json)
        if: always()
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add docs/blog_social_state.json
          if git diff --cached --quiet; then
            echo "Sin cambios en docs/blog_social_state.json"
          else
            git commit -m "chore: actualizar estado de publicación en redes"
            git push
          fi
