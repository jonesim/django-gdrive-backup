import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="django-gdrive-backup",
    version="0.0.8",
    author="Ian Jones",
    description=("Backs up django postgres databases, local folders and S3 folders to a Google Drive folder "
                 "through a google service account."),
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/jonesim/django-gdrive-backup",
    include_package_data=True,
    packages=['gdrive_backup', 'gdrive_backup.management', 'gdrive_backup.management.commands'],
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires='>=3.6',
    install_requires=['encrypted-credentials', 'google-client-helper', 'requests'],
)