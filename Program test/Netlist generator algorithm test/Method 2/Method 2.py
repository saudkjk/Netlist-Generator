import os
import cv2
import numpy as np
import json
from ultralytics import YOLO
from scipy.ndimage import label as connected_label
import matplotlib.pyplot as plt
from matplotlib import cm
from scipy.spatial import distance

# Define helper functions
def visualize_everything_connected(image_path, json_path):
    with open(json_path, "r") as f:
        components = json.load(f)

    image = cv2.imread(image_path, cv2.IMREAD_COLOR)
    grayscale = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(grayscale, 50, 150)
    kernel = np.ones((5, 5), np.uint8)
    connected_edges = cv2.dilate(edges, kernel, iterations=2)
    overlay = image.copy()
    overlay[connected_edges > 0] = [0, 255, 0]

    return connected_edges, components

def mask_components(connected_edges, components):
    masked_edges = connected_edges.copy()
    for component in components:
        bbox = component["bounding_box"]
        x1, y1, x2, y2 = bbox
        width = x2 - x1
        height = y2 - y1
        shrink_x = 12 if width >= height else 5
        shrink_y = 12 if height >= width else 5
        shrink_x1 = max(x1 + shrink_x, 0)
        shrink_y1 = max(y1 + shrink_y, 0)
        shrink_x2 = max(x2 - shrink_x, 0)
        shrink_y2 = max(y2 - shrink_y, 0)
        cv2.rectangle(masked_edges, (shrink_x1, shrink_y1), (shrink_x2, shrink_y2), 0, -1)

    return masked_edges

def draw_detected_components(image_path, components):
    image = cv2.imread(image_path, cv2.IMREAD_COLOR)
    for component in components:
        label = component["label"]
        x1, y1, x2, y2 = component["bounding_box"]
        cv2.rectangle(image, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.putText(image, label, (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 1)
        keypoints = component["connection_points"]
        for point in keypoints:
            cv2.circle(image, (point[0], point[1]), 5, (255, 0, 0), -1)

    return image

def is_point_connected(masked_edges, px, py, kernel_size=3):
    half_size = kernel_size // 2
    neighborhood = masked_edges[
        max(0, py - half_size): min(masked_edges.shape[0], py + half_size + 1),
        max(0, px - half_size): min(masked_edges.shape[1], px + half_size + 1)
    ]

    return np.any(neighborhood > 0)


def find_nearest_edge(masked_edges, px, py):
    edge_points = np.argwhere(masked_edges > 0)  # Get all edge points
    if edge_points.size == 0:
        return None  # No edges in the mask
    distances = distance.cdist([(py, px)], edge_points)  # Compute distances from the point to all edges
    nearest_index = np.argmin(distances)  # Index of the nearest edge
    
    return edge_points[nearest_index]  # Coordinates of the nearest edge (y, x)

def is_point_connected_or_nearest(masked_edges, px, py, kernel_size=3):
    # Check local connection first
    half_size = kernel_size // 2
    neighborhood = masked_edges[
        max(0, py - half_size): min(masked_edges.shape[0], py + half_size + 1),
        max(0, px - half_size): min(masked_edges.shape[1], px + half_size + 1)
    ]

    if np.any(neighborhood > 0):  # Direct connection
        return True, (px, py)

    # Find the nearest edge if not connected
    nearest_edge = find_nearest_edge(masked_edges, px, py)
    if nearest_edge is not None:
        return True, (nearest_edge[1], nearest_edge[0])  # Return x, y of the nearest edge
    
    return False, None  # No connection and no nearest edge

def visualize_connected_regions(masked_edges, labeled_edges, components):
    """
    Visualize regions that are connected to electrical components.
    Ground node regions are highlighted with white color, and other regions are given unique colors.
    Each region is also labeled with its ID for easy distinction.
    """
    # Get unique regions
    unique_regions = np.unique(labeled_edges)
    
    # Generate a unique colormap for all regions
    colormap = cm.get_cmap('tab20', len(unique_regions))  # Choose a color map with many distinct colors
    colors = (colormap(np.linspace(0, 1, len(unique_regions)))[:, :3] * 255).astype(np.uint8)
    
    # Create an RGB image for visualization
    region_image = np.zeros((*labeled_edges.shape, 3), dtype=np.uint8)

    connected_regions = set()
    gnd_regions = set()

    # Check which regions are connected to electrical components
    for component in components:
        for point in component["connection_points"]:
            px, py = point
            if py >= labeled_edges.shape[0] or px >= labeled_edges.shape[1]:
                continue  # Out of bounds

            region = labeled_edges[py, px]
            if region > 0:  # Ignore the background
                connected_regions.add(region)

                # Mark ground regions
                if component["label"].upper() == "GND":
                    gnd_regions.add(region)

    # Highlight regions with electrical connections
    for region_index, region_id in enumerate(connected_regions):
        if region_id == 0:  # Skip background
            continue

        # Mask for the current region
        mask = labeled_edges == region_id

        if region_id in connected_regions:
            # Use white color for ground regions
            if region_id in gnd_regions:
                region_image[mask] = [255, 255, 255]  # White for ground regions
            else:
                # Assign a unique color for each connected region
                region_color = colors[region_id % len(colors)]
                region_image[mask] = region_color

        # Add region label at the center of the region
        y, x = np.argwhere(mask).mean(axis=0).astype(int)  # Find the center of the region
        cv2.putText(region_image, str(region_index+1), (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA)  # Black outline
        cv2.putText(region_image, str(region_index+1), (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)  # White text

    return region_image

def overlay_and_find_nodes_with_connected_regions(connected_edges, masked_edges, components, test_results_path, image_file, output_files_path):
    labeled_edges, num_regions = connected_label(masked_edges)

    region_to_node = {}
    unique_nodes = set()
    current_node_id = 1

    connected_regions = set()
    gnd_regions = set()
    for component in components:
        for point in component["connection_points"]:
            px, py = point
            if py >= labeled_edges.shape[0] or px >= labeled_edges.shape[1]:
                continue

            region = labeled_edges[py, px]
            if region > 0:
                connected_regions.add(region)
                if component["label"].upper() == "GND":
                    gnd_regions.add(region)

    image_output_folder = os.path.join(output_files_path, os.path.splitext(image_file)[0])
    os.makedirs(image_output_folder, exist_ok=True)
    region_image_path = os.path.join(image_output_folder, 'region_image.jpg')
    region_image = visualize_connected_regions(masked_edges, labeled_edges, components)
    cv2.imwrite(region_image_path, region_image)
    
    image_results_file = os.path.join(test_results_path, os.path.splitext(image_file)[0] + '.txt')
    
    with open(image_results_file, 'w') as results_file:
        for component in components:
            # Skip GND components
            if component["label"].upper() == "GND":
                continue
            
            connected_nodes = []
            for point in component["connection_points"]:
                px, py = point
                if py >= labeled_edges.shape[0] or px >= labeled_edges.shape[1]:
                    continue
                
                is_connected, connection_point = is_point_connected_or_nearest(masked_edges, px, py)
                if is_connected:
                    connected_px, connected_py = connection_point
                    region = labeled_edges[connected_py, connected_px]  # Use the connected or nearest edge point
                    if region > 0:
                        if region not in region_to_node:
                            region_to_node[region] = current_node_id
                            current_node_id += 1
                        node_id = region_to_node[region]
                        unique_nodes.add(node_id)
                        connected_nodes.append(node_id)
    
            # Ensure we only write components that have at least one connected node
            connected_nodes = list(set(connected_nodes))
            if connected_nodes:  # Check if there are any connected nodes
                unique_label = component["label"]
                results_file.write(f"{unique_label} {' '.join(map(str, connected_nodes))}\n")

    image_results_file = os.path.join(test_results_path, os.path.splitext(image_file)[0] + '.txt')
    
            
    node_image = cv2.cvtColor(connected_edges, cv2.COLOR_GRAY2BGR)

    # Rearrange node IDs based on top-left-most pixel
    region_top_left = {}
    for region in range(1, num_regions + 1):
        pixels = np.argwhere(labeled_edges == region)  # Get all pixels in the region
        if len(pixels) > 0:
            top_left_pixel = pixels[np.lexsort((pixels[:, 1], pixels[:, 0]))][0]  # Sort by (y, then x) and pick the first
            region_top_left[region] = top_left_pixel

    # Sort regions by top-left-most pixel
    sorted_regions = sorted(region_top_left.items(), key=lambda x: (x[1][0], x[1][1]))  # Sort by (y, x)
    
    # Reassign node IDs
    new_region_to_node = {}
    new_node_id = 1
    for region, _ in sorted_regions:
        if region in region_to_node:
            new_region_to_node[region] = new_node_id
            new_node_id += 1

    # Update visualization with new IDs
    for region, node_id in new_region_to_node.items():
        color = (0, 0, 255)  # Blue for newly ordered nodes
        mask = labeled_edges == region
        node_image[mask] = color
    
    # Draw red dots and label nodes on the image
    for region, node_id in new_region_to_node.items():
        top_left_pixel = region_top_left[region]
        y, x = top_left_pixel
        cv2.circle(node_image, (x, y), 7, (0, 0, 255), -1)  # Draw a red dot (BGR: (0, 0, 255))
        cv2.putText(node_image, str(node_id), (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA)  # Black outline
        cv2.putText(node_image, str(node_id), (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)  # White text

    # Save the updated image with red dots and labels
    labeled_image_path = os.path.join(image_output_folder, 'labeled_nodes_image.jpg')
    cv2.imwrite(labeled_image_path, node_image)

    # Create a new text file for adjusted node positions
    adjusted_results_file = os.path.join(test_results_path, os.path.splitext(image_file)[0] + '_adjusted.txt')
    
    with open(adjusted_results_file, 'w') as adjusted_results:
        for component in components:
            # Skip GND components
            if component["label"].upper() == "GND":
                continue
            
            adjusted_connected_nodes = []
            for point in component["connection_points"]:
                px, py = point
                if py >= labeled_edges.shape[0] or px >= labeled_edges.shape[1]:
                    continue
                
                is_connected, connection_point = is_point_connected_or_nearest(masked_edges, px, py)
                if is_connected:
                    connected_px, connected_py = connection_point
                    region = labeled_edges[connected_py, connected_px]  # Use the connected or nearest edge point
                    if region > 0 and region in new_region_to_node:
                        node_id = new_region_to_node[region]
                        adjusted_connected_nodes.append(node_id)
            
            # Ensure we only write components that have at least one connected node
            adjusted_connected_nodes = list(set(adjusted_connected_nodes))
            if adjusted_connected_nodes:  # Check if there are any connected nodes
                unique_label = component["label"]
                adjusted_results.write(f"{unique_label} {' '.join(map(str, adjusted_connected_nodes))}\n")

    # Return the original output with updated node IDs
    return node_image, len(unique_nodes)

def process_all_images(test_images_folder, model_path, output_files_path, test_results_path):
    os.makedirs(output_files_path, exist_ok=True)
    os.makedirs(test_results_path, exist_ok=True)

    model = YOLO(model_path)

    image_files = [f for f in os.listdir(test_images_folder) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]

    for image_file in image_files:
        image_path = os.path.join(test_images_folder, image_file)
        image_output_folder = os.path.join(output_files_path, os.path.splitext(image_file)[0])
        os.makedirs(image_output_folder, exist_ok=True)

        json_path = os.path.join(image_output_folder, 'circuit_info.json')
        annotated_image_path = os.path.join(image_output_folder, 'annotated.jpg')
        node_image_path = os.path.join(image_output_folder, 'nodes.jpg')

        results = model(image_path)[0]
        circuit_info = []

        for result in results:
            for cls, keypoints, bbox in zip(result.boxes.cls.cpu().numpy(),
                                             result.keypoints.xy.cpu().numpy(),
                                             result.boxes.xyxy.cpu().numpy()):
                class_idx = int(cls)
                object_name = results.names[class_idx]

                x_min, y_min, x_max, y_max = map(int, bbox)
                bounding_box = [x_min, y_min, x_max, y_max]

                connection_points = [
                    [int(point[0]), int(point[1])] for point in keypoints if not (point[0] == 0 and point[1] == 0)
                ]

                circuit_info.append({
                    "label": object_name,
                    "bounding_box": bounding_box,
                    "connection_points": connection_points
                })

        with open(json_path, 'w') as json_file:
            json.dump(circuit_info, json_file, indent=4)

        annotated_image = draw_detected_components(image_path, circuit_info)
        connected_edges, components = visualize_everything_connected(image_path, json_path)
        masked_edges = mask_components(connected_edges, components)
        node_image, num_nodes = overlay_and_find_nodes_with_connected_regions(
            connected_edges, masked_edges, components, test_results_path, image_file, output_files_path)

        cv2.imwrite(annotated_image_path, annotated_image)
        cv2.imwrite(node_image_path, node_image)

if __name__ == '__main__':
    parent_dir = os.path.dirname(os.getcwd()) # Parent directory
    PROJECT_PATH = os.path.dirname(os.path.dirname(parent_dir))  # Project path is three levels up
    pose_folder = os.path.join(PROJECT_PATH, 'Current trained model/pose')
    train_folders = [folder for folder in os.listdir(pose_folder) if folder.startswith('train')]

    # Check if there are train folders
    if not train_folders:
        raise FileNotFoundError("No 'train' folders found in the pose directory.")

    # Determine the latest train folder
    def extract_suffix(folder_name):
        if folder_name == "train":
            return 0
        else:
            return int(folder_name[5:])

    latest_train_folder = max(train_folders, key=extract_suffix)
    latest_train_path = os.path.join(pose_folder, latest_train_folder, 'weights', 'last.pt')

    test_images_folder = os.path.join(parent_dir, 'Test images/')
    output_files_path = os.path.join(parent_dir, 'Method 2/Test outputs for debugging/')
    test_results_path = os.path.join(parent_dir, 'Method 2/Test results/')

    process_all_images(test_images_folder, latest_train_path, output_files_path, test_results_path)