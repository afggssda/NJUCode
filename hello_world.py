def generate_pascals_triangle(n):
    """Generate Pascal's Triangle with n rows."""
    if n <= 0:
        return []
    
    triangle = [[1]]
    
    for i in range(1, n):
        row = [1]
        for j in range(1, i):
            row.append(triangle[i - 1][j - 1] + triangle[i - 1][j])
        row.append(1)
        triangle.append(row)
        
    return triangle


if __name__ == "__main__":
    # Set the number of rows to display
    num_rows = 5
    
    # Generate and print the triangle
    result = generate_pascals_triangle(num_rows)
    
    print(f"Pascal's Triangle ({num_rows} rows):")
    for line in result:
        print(" ".join(str(num) for num in line))
