<?php
/**
 * Plugin Name: ValenciaTribe — Datos estructurados y Jerarquía SEO H1
 * Description: Imprime el JSON-LD (FAQ/TouristTrip/Breadcrumb) y asegura etiquetas H1 limpias en productos y páginas.
 */

// 1. JSON-LD de productos
add_action('wp_head', function () {
    if (! function_exists('is_product') || ! is_product()) { return; }
    $id = get_queried_object_id();
    $product = wc_get_product($id);
    if (! $product) { return; }
    
    // Salvaguarda: si el precio está vacío o no es numérico, omitir para evitar errores fatales
    $price = $product->get_price();
    if (empty($price) || !is_numeric($price)) {
        return;
    }
    
    $ld = get_post_meta($id, '_tribal_jsonld', true);
    if (! empty($ld)) {
        echo "\n<script type=\"application/ld+json\">" . $ld . "</script>\n";
    }
}, 20);

// 2. Jerarquía H1 en Elementor para Fichas de Producto y Páginas
add_filter('elementor/widget/render_content', function ($widget_content, $widget) {
    // Fichas de Producto de WooCommerce
    if ('woocommerce-product-title' === $widget->get_name()) {
        $widget_content = preg_replace('/<h2(\s+class="[^"]*product_title[^"]*")/i', '<h1$1', $widget_content);
        $widget_content = preg_replace('/<\/h2>(\s*)$/i', '</h1>$1', $widget_content);
        return $widget_content;
    }
    
    // Título principal en Páginas (Template de Elementor The7: c4b3485, 3ade1ec o tag dynamic post-title)
    if ('heading' === $widget->get_name()) {
        $is_post_title = false;
        if (in_array($widget->get_id(), array('c4b3485', '3ade1ec'))) {
            $is_post_title = true;
        } else {
            $dynamic = $widget->get_settings('__dynamic__');
            if (is_array($dynamic) && isset($dynamic['title']) && strpos($dynamic['title'], 'post-title') !== false) {
                $is_post_title = true;
            }
        }
        
        if ($is_post_title) {
            $widget_content = preg_replace('/<h2(\s+class="[^"]*elementor-heading-title[^"]*")/i', '<h1$1', $widget_content);
            $widget_content = preg_replace('/<\/h2>(\s*)$/i', '</h1>$1', $widget_content);
        }
    }
    
    return $widget_content;
}, 20, 2);
